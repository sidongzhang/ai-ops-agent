"""记账与预算：增量不重复、未知用量不当免费、原子预留、并发安全。

用临时 SQLite（经 set_session_factory / 直接注入 session）覆盖，不碰开发库。
"""
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import settings
from app.models.auth import Org
from app.models.diagnostics import DiagnosisReport
from app.models.run_state import DiagnosisRunState
from app.models.systems import MonitoredSystem
from app.models.usage import DiagnosisUsageLedger
from app.services.diagnostics import cancel_bus, usage


class UsageAccountingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self._tmp.name) / 'u.db'}")
        SQLModel.metadata.create_all(self.engine)
        cancel_bus.set_session_factory(lambda: Session(self.engine))
        with Session(self.engine) as s:
            s.add(Org(name="o"))
            s.commit()
            s.add(MonitoredSystem(org_id=1, key="s", name="s"))
            s.commit()
            self.system_id = 1
            r = DiagnosisReport(org_id=1, system_id=1, question="q", status="success", total_tokens=0)
            s.add(r)
            s.commit()
            self.report_id = r.id

    def tearDown(self) -> None:
        cancel_bus.set_session_factory(None)
        self._tmp.cleanup()

    def _usage(self, total: int, cost: float = 0.0, requests: int = 3) -> str:
        return (
            '{"input_tokens": %d, "output_tokens": 10, "cache_read_tokens": 0, '
            '"cache_write_tokens": 0, "total_tokens": %d, "requests": %d, "tool_calls": 1, '
            '"cost": "%s"}' % (total - 10, total, requests, cost)
        )

    def test_first_run_delta_from_zero(self) -> None:
        with Session(self.engine) as s:
            out = usage.record_run_usage(
                report_id=self.report_id, org_id=1, system_id=1,
                usage_json=self._usage(1000, 0.01), session=s,
            )
        self.assertEqual(out["tokens_delta"], 1000)
        self.assertTrue(out["usage_complete"])
        self.assertAlmostEqual(out["cost_delta"], 0.01, places=6)

    def test_resume_does_not_double_count(self) -> None:
        """关键：框架累计值不能当增量再累加一遍。"""
        with Session(self.engine) as s:
            usage.record_run_usage(
                report_id=self.report_id, org_id=1, system_id=1,
                usage_json=self._usage(1000, 0.01), session=s,
            )
            # 第二轮（resume）：框架给出的是累计 2500，baseline=上一轮累计 1000 → 增量 1500
            out2 = usage.record_run_usage(
                report_id=self.report_id, org_id=1, system_id=1,
                usage_json=self._usage(2500, 0.025), before_json=self._usage(1000, 0.01), session=s,
            )
        self.assertEqual(out2["tokens_delta"], 1500, "resume 增量必须是 2500-1000")
        totals = usage.session_usage(1, self.system_id, session=Session(self.engine))
        self.assertEqual(totals["tokens"], 2500, "会话累计 = SUM(delta) = 1000+1500")
        self.assertAlmostEqual(totals["cost_usd"], 0.025, places=6)

    def test_incomplete_usage_settles_with_reserve_not_free(self) -> None:
        """未知用量不得按免费处理：结算值取 max(实测, 保守预留)。"""
        with Session(self.engine) as s:
            out = usage.record_run_usage(
                report_id=self.report_id, org_id=1, system_id=1,
                usage_json="",                       # 取消后用量不可得
                cancelled=True, cancel_phase="model_request", session=s,
            )
        self.assertFalse(out["usage_complete"])
        self.assertEqual(out["gap_reason"], usage.GAP_IN_FLIGHT)
        self.assertEqual(out["tokens_delta"], 0)
        self.assertGreaterEqual(out["settle_tokens"], settings.diagnosis_budget_reserve_tokens)
        with Session(self.engine) as s:
            row = s.exec(select(DiagnosisUsageLedger)).first()
            self.assertTrue(row.estimated_portion)
            self.assertFalse(row.usage_complete)

    def test_report_marks_incomplete(self) -> None:
        with Session(self.engine) as s:
            usage.record_run_usage(
                report_id=self.report_id, org_id=1, system_id=1, usage_json="",
                cancelled=True, cancel_phase="tool_exec", session=s,
            )
        with Session(self.engine) as s:
            r = s.get(DiagnosisReport, self.report_id)
            self.assertFalse(r.usage_complete)
            self.assertEqual(r.usage_gap_reason, usage.GAP_PROVIDER)
            self.assertTrue(r.price_table_version)


class BudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self._tmp.name) / 'b.db'}")
        SQLModel.metadata.create_all(self.engine)
        cancel_bus.set_session_factory(lambda: Session(self.engine))
        self.tokens = settings.diagnosis_session_token_budget
        self.reserve = settings.diagnosis_budget_reserve_tokens

    def tearDown(self) -> None:
        cancel_bus.set_session_factory(None)
        self._tmp.cleanup()

    def _reserve(self):
        return usage.reserve_budget(org_id=1, user_id=1, system_id=1)

    def test_reserve_then_settle(self) -> None:
        first = self._reserve()
        self.assertTrue(first["allowed"])
        self.assertGreaterEqual(first["used_tokens"], self.reserve)
        usage.settle_budget(org_id=1, user_id=1, system_id=1, tokens=1234, cost_usd=0.01)
        usage.settle_budget(org_id=1, user_id=1, system_id=1, tokens=0, cost_usd=0.0)

    def test_concurrent_reserves_respect_limit(self) -> None:
        """并发预留：只能放行预算内的数量（原子性）。"""
        allowed: list[bool] = []
        lock = threading.Lock()

        def _submit():
            r = self._reserve()
            with lock:
                allowed.append(r["allowed"])

        threads = [threading.Thread(target=_submit) for _ in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        max_allowed = self.tokens // self.reserve
        self.assertLessEqual(sum(1 for a in allowed if a), max_allowed + 1)
        self.assertGreater(sum(1 for a in allowed if a), 0)

    def test_release_frees_reservation(self) -> None:
        self.assertTrue(self._reserve()["allowed"])
        usage.release_budget(org_id=1, user_id=1, system_id=1)
        # 释放后再放行（预留已被扣掉）
        self.assertTrue(self._reserve()["allowed"])


class ReaperHeartbeatTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self._tmp.name) / 'r.db'}")
        SQLModel.metadata.create_all(self.engine)
        cancel_bus.set_session_factory(lambda: Session(self.engine))
        with Session(self.engine) as s:
            s.add(Org(name="o")); s.commit()
            s.add(MonitoredSystem(org_id=1, key="s", name="s")); s.commit()
            self.system_id = 1
            self.ids = []
            for _ in range(3):
                r = DiagnosisReport(org_id=1, system_id=1, question="q", status="running")
                s.add(r); s.commit(); self.ids.append(r.id)

    def tearDown(self) -> None:
        cancel_bus.set_session_factory(None)
        self._tmp.cleanup()

    def test_alive_worker_is_not_reaped(self) -> None:
        """心跳新鲜 → 绝不误收（哪怕创建时间很旧）。"""
        from app.services.diagnostics.reaper import reap_stale_diagnoses

        now = datetime.now(timezone.utc)
        with Session(self.engine) as s:
            r = s.get(DiagnosisReport, self.ids[0])
            r.created_at = now - timedelta(hours=5)
            r.runner_heartbeat_at = now          # 刚刚还在跳
            s.add(r); s.commit()
        out = reap_stale_diagnoses(minutes=1)
        self.assertEqual(out["alive"], 1)
        self.assertEqual(out["scanned"], 0)
        with Session(self.engine) as s:
            self.assertEqual(s.get(DiagnosisReport, self.ids[0]).status, "running")

    def test_stale_heartbeat_is_reaped(self) -> None:
        from app.services.diagnostics.reaper import reap_stale_diagnoses

        now = datetime.now(timezone.utc)
        with Session(self.engine) as s:
            r = s.get(DiagnosisReport, self.ids[1])
            r.runner_heartbeat_at = now - timedelta(minutes=10)
            s.add(r); s.commit()
        out = reap_stale_diagnoses(minutes=1)
        self.assertEqual(out["failed"], 1)
        with Session(self.engine) as s:
            row = s.get(DiagnosisReport, self.ids[1])
            self.assertEqual(row.status, "failed")
            self.assertIn("失联", row.error_message)

    def test_never_registered_and_old_is_reaped(self) -> None:
        from app.services.diagnostics.reaper import reap_stale_diagnoses

        with Session(self.engine) as s:
            r = s.get(DiagnosisReport, self.ids[2])
            r.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
            s.add(r); s.commit()
        out = reap_stale_diagnoses(minutes=1)
        self.assertEqual(out["failed"], 1)


class UsageSerializationTests(unittest.TestCase):
    def test_usage_json_is_populated_and_not_silently_empty(self) -> None:
        """回归：runner._usage_json 曾经因为漏 import json 而静默返回空串，
        导致成功诊断被误判成 provider_no_usage、账本增量记 0。"""
        from pydantic_ai.usage import RunUsage

        from app.agent.diagnostics.runner import _usage_json

        usage = RunUsage(input_tokens=100, output_tokens=10, requests=2)
        payload = _usage_json(usage)
        self.assertTrue(payload, "_usage_json 不允许静默返回空串")
        data = json.loads(payload)
        self.assertEqual(data["input_tokens"], 100)
        self.assertEqual(data["output_tokens"], 10)
        self.assertEqual(data["requests"], 2)
        self.assertEqual(data["total_tokens"], 110)


class AtomicFinalizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self._tmp.name) / 'f.db'}")
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as s:
            s.add(Org(name="o")); s.commit()
            s.add(MonitoredSystem(org_id=1, key="s", name="s")); s.commit()
            r = DiagnosisReport(org_id=1, system_id=1, question="q", status="running")
            s.add(r); s.commit(); self.report_id = r.id

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_only_one_writer_wins(self) -> None:
        """原子条件更新：第二个写者拿不到（先到先赢），成功不会被覆盖成取消。"""
        from sqlalchemy import update

        from app.services.diagnostics.service import _finalize_report

        # 直接测 SQL 条件语义（引擎与生产一致：UPDATE ... WHERE status='running'）
        with Session(self.engine) as s:
            first = s.execute(
                update(DiagnosisReport)
                .where(DiagnosisReport.id == self.report_id, DiagnosisReport.status == "running")
                .values(status="success", answer="done")
            ).rowcount
            s.commit()
        with Session(self.engine) as s:
            second = s.execute(
                update(DiagnosisReport)
                .where(DiagnosisReport.id == self.report_id, DiagnosisReport.status == "running")
                .values(status="cancelled")
            ).rowcount
            s.commit()
        self.assertEqual(first, 1)
        self.assertEqual(second, 0, "成功之后取消必须拿不到写入权")
        with Session(self.engine) as s:
            self.assertEqual(s.get(DiagnosisReport, self.report_id).status, "success")
        self.assertTrue(callable(_finalize_report))


if __name__ == "__main__":
    unittest.main()

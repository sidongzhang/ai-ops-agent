"""继续（resume）：幂等只启动一次、不可恢复则明确拒绝、过期证据标注/重取、增量不重复。

用临时 SQLite（注入会话），不碰开发库，也不消耗 LLM 额度。
"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.auth import Org
from app.models.diagnostics import DiagnosisReport
from app.models.run_state import DiagnosisRunState
from app.models.systems import MonitoredSystem
from app.services.diagnostics import resume as resume_mod
from app.services.diagnostics import session as session_mod
from app.services.diagnostics.service import start_resume


def _history_json(*, tool: str = "list_services", age_minutes: float = 30.0) -> str:
    """造一段真实结构的历史：用户提问 → 模型发起工具调用 → 工具结果（可指定年龄）→ 模型回复。"""
    from pydantic_ai.messages import UserPromptPart

    stamp = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    history = [
        ModelRequest(parts=[UserPromptPart("TCAT 为什么变慢？")]),
        ModelResponse(parts=[ToolCallPart(tool, {}, tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart(tool, "ok", tool_call_id="c1", timestamp=stamp)]),
        ModelResponse(parts=[TextPart("部分结论：还在排查")]),
    ]
    return ModelMessagesTypeAdapter.dump_json(history).decode("utf-8")


class ResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self._tmp.name) / 'r.db'}")
        SQLModel.metadata.create_all(self.engine)
        session_mod.set_session_factory(lambda: Session(self.engine))
        with Session(self.engine) as s:
            s.add(Org(name="o")); s.commit()
            s.add(MonitoredSystem(org_id=1, key="s", name="s")); s.commit()
            self.system_id = 1
            src = DiagnosisReport(
                org_id=1, system_id=1, question="TCAT 为什么变慢？", status="cancelled",
                answer="（诊断已被用户中断）", total_tokens=0, cancel_phase="model_request",
            )
            s.add(src); s.commit()
            self.source_id = src.id
            s.add(DiagnosisRunState(
                report_id=src.id,
                messages_json=_history_json(),
                conversation_id="conv-1",
                framework_cumulative_json=json.dumps(
                    {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100,
                     "requests": 2, "tool_calls": 1, "cost": "0.01"}
                ),
                resumable=True,
            ))
            s.commit()

    def tearDown(self) -> None:
        session_mod.set_session_factory(None)
        self._tmp.cleanup()

    # ── ① 幂等：重复/并发只启动一次 ────────────────────────────────────
    def test_repeated_resume_starts_once(self) -> None:
        with Session(self.engine) as s:
            first, payload1 = start_resume(s, self.source_id, self.system_id, 1, actor_id="1")
            second, payload2 = start_resume(s, self.source_id, self.system_id, 1, actor_id="1")
        self.assertIsNotNone(payload1, "首次应真正启动")
        self.assertIsNone(payload2, "第二次不应再启动执行")
        self.assertEqual(first.id, second.id, "两次应指向同一条报告")
        with Session(self.engine) as s:
            rows = s.exec(
                select(DiagnosisReport).where(DiagnosisReport.resumed_from_report_id == self.source_id)
            ).all()
        self.assertEqual(len(rows), 1, "唯一约束保证只产生一条续跑报告")
        self.assertEqual(rows[0].status, "running")

    # ── ② 不可恢复：明确拒绝，不制造"必然失败的继续" ────────────────────
    def test_rejects_when_history_missing(self) -> None:
        with Session(self.engine) as s:
            s.get(DiagnosisRunState, self.source_id).messages_json = ""
            s.add(s.get(DiagnosisRunState, self.source_id)); s.commit()
        with Session(self.engine) as s:
            with self.assertRaises(resume_mod.ResumeNotPossible) as ctx:
                start_resume(s, self.source_id, self.system_id, 1)
        self.assertEqual(ctx.exception.reason, "no_history")
        self.assertIn("未取得可恢复历史", str(ctx.exception))

    def test_rejects_when_history_corrupted(self) -> None:
        with Session(self.engine) as s:
            state = s.get(DiagnosisRunState, self.source_id)
            state.messages_json = "{不是合法历史}"
            s.add(state); s.commit()
        with Session(self.engine) as s:
            with self.assertRaises(resume_mod.ResumeNotPossible):
                start_resume(s, self.source_id, self.system_id, 1)

    def test_rejects_when_source_succeeded(self) -> None:
        with Session(self.engine) as s:
            src = s.get(DiagnosisReport, self.source_id)
            src.status = "success"
            s.add(src); s.commit()
        with Session(self.engine) as s:
            with self.assertRaises(resume_mod.ResumeNotPossible) as ctx:
                start_resume(s, self.source_id, self.system_id, 1)
        self.assertIn("无需继续", str(ctx.exception))

    # ── ③ 过期证据：标注 + force_refresh 失效 ─────────────────────────
    def test_stale_evidence_is_annotated(self) -> None:
        with Session(self.engine) as s:
            payload = resume_mod.prepare_resume(s, s.get(DiagnosisReport, self.source_id))
        self.assertEqual(len(payload["stale"]), 1)
        self.assertEqual(payload["stale"][0]["tool"], "list_services")
        self.assertIn("可能已过期", payload["note"])
        self.assertIn("list_services", payload["note"])
        # 未开 force_refresh：历史内容保持原样
        self.assertIn("ok", (payload["history_json"] or ""))

    def test_force_refresh_invalidates_stale_result(self) -> None:
        with Session(self.engine) as s:
            payload = resume_mod.prepare_resume(
                s, s.get(DiagnosisReport, self.source_id), force_refresh=True
            )
        from pydantic_ai.messages import ModelMessagesTypeAdapter

        history = ModelMessagesTypeAdapter.validate_json(payload["history_json"])
        returns = [
            p for m in history for p in getattr(m, "parts", [])
            if isinstance(p, ToolReturnPart)
        ]
        self.assertTrue(returns)
        self.assertIn("需要时请重新调用工具取证", str(returns[0].content))
        # 配对仍然合法（tool_call_id 未变）
        self.assertEqual(returns[0].tool_call_id, "c1")

    def test_fresh_evidence_is_not_flagged(self) -> None:
        with Session(self.engine) as s:
            state = s.get(DiagnosisRunState, self.source_id)
            state.messages_json = _history_json(age_minutes=0.5)   # 30 秒前，远小于 TTL
            s.add(state); s.commit()
            payload = resume_mod.prepare_resume(s, s.get(DiagnosisReport, self.source_id))
        self.assertEqual(payload["stale"], [])

    def test_tool_classification_by_ttl(self) -> None:
        self.assertEqual(resume_mod._ttl_seconds("list_services"), 180)      # health
        self.assertEqual(resume_mod._ttl_seconds("query_prometheus"), 300)   # metrics
        self.assertEqual(resume_mod._ttl_seconds("query_business_dataset"), 900)  # business
        self.assertEqual(resume_mod._ttl_seconds("get_service_topology"), 3600)   # static

    # ── ④ 累计用量：baseline 来自源报告，增量不重复 ────────────────────
    def test_resume_payload_carries_baseline(self) -> None:
        with Session(self.engine) as s:
            payload = resume_mod.prepare_resume(s, s.get(DiagnosisReport, self.source_id))
        self.assertEqual(payload["prior_usage"].requests, 2)
        self.assertEqual(payload["prior_usage"].input_tokens, 1000)
        self.assertIn("1100", payload["baseline_json"])

    def test_resume_ledger_delta_excludes_prior(self) -> None:
        from app.models.usage import DiagnosisUsageLedger
        from app.services.diagnostics.usage import record_run_usage

        with Session(self.engine) as s:
            new_report = DiagnosisReport(
                org_id=1, system_id=1, question="继续", status="success",
                resumed_from_report_id=self.source_id,
            )
            s.add(new_report); s.commit()
            new_id = new_report.id
            # 框架返回的是**累计**（含 prior 的 1100）→ 增量应为 2900
            record_run_usage(
                report_id=new_id, org_id=1, system_id=1, run_kind="resume",
                usage_json=json.dumps({"input_tokens": 3000, "output_tokens": 200,
                                       "total_tokens": 4000, "requests": 4, "cost": "0.03"}),
                before_json=json.dumps({"input_tokens": 1000, "output_tokens": 100,
                                        "total_tokens": 1100, "requests": 2, "cost": "0.01"}),
                session=s,
            )
            row = s.exec(
                select(DiagnosisUsageLedger).where(DiagnosisUsageLedger.report_id == new_id)
            ).first()
        self.assertEqual(row.run_kind, "resume")
        self.assertEqual(row.tokens_delta, 2900, "增量 = 累计 4000 − baseline 1100")
        self.assertAlmostEqual(row.cost_usd_delta, 0.02, places=6)


if __name__ == "__main__":
    unittest.main()

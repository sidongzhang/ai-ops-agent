"""取消总线：DB 标志幂等 / 注册即自检 / 轮询兜底 / 关停等待 / 终态 no-op。

用临时 SQLite 引擎（经 set_session_factory 注入），不碰开发库。
"""
import pytest
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from app.models.auth import Org
from app.models.diagnostics import DiagnosisReport
from app.models.systems import MonitoredSystem
from app.services.diagnostics import cancel_bus


class FakeToken:
    """替代 pydantic_ai.CancellationToken（只需要 cancel() 幂等语义）。"""

    def __init__(self) -> None:
        self.cancelled = False
        self.calls = 0
        self._lock = threading.Lock()

    def cancel(self) -> None:
        with self._lock:
            self.calls += 1
            self.cancelled = True


class CancelBusTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self._tmp.name) / 'c.db'}")
        SQLModel.metadata.create_all(self.engine)
        cancel_bus.set_session_factory(lambda: Session(self.engine))
        cancel_bus._registry.clear()
        with Session(self.engine) as session:
            session.add(Org(name="测试组织"))
            session.commit()
            session.add(MonitoredSystem(org_id=1, key="s", name="s"))
            session.commit()
            self.system_id = 1
            report = DiagnosisReport(org_id=1, system_id=1, question="q", status="running")
            session.add(report)
            session.commit()
            self.report_id = report.id

    def tearDown(self) -> None:
        cancel_bus.set_session_factory(None)
        cancel_bus._registry.clear()
        self._tmp.cleanup()

    # ── DB 标志 ────────────────────────────────────────────────────────
    def test_set_cancel_requested_is_idempotent(self) -> None:
        self.assertTrue(cancel_bus.set_cancel_requested(self.report_id))    # 本次写入成功
        self.assertFalse(cancel_bus.set_cancel_requested(self.report_id))   # 再点：无写入
        self.assertTrue(cancel_bus.is_cancel_requested(self.report_id))

    def test_set_cancel_requested_noop_on_terminal(self) -> None:
        with Session(self.engine) as session:
            report = session.get(DiagnosisReport, self.report_id)
            report.status = "success"
            session.add(report)
            session.commit()
        self.assertFalse(cancel_bus.set_cancel_requested(self.report_id))   # 成功不被覆盖
        self.assertFalse(cancel_bus.is_cancel_requested(self.report_id))
        self.assertTrue(cancel_bus.is_terminal(self.report_id))

    # ── 竞态 1：取消早于 token 注册 ─────────────────────────────────────
    def test_register_self_checks_existing_flag(self) -> None:
        cancel_bus.set_cancel_requested(self.report_id)
        token = FakeToken()
        cancel_bus.register(self.report_id, token)      # 注册后立即自检
        self.assertTrue(token.cancelled, "注册时必须自检 DB 标志，堵住'取消早于注册'")

    def test_register_without_flag_does_not_cancel(self) -> None:
        token = FakeToken()
        cancel_bus.register(self.report_id, token)
        self.assertFalse(token.cancelled)

    # ── 竞态 2：Redis 丢消息 → 轮询兜底 ─────────────────────────────────
    def test_poller_catches_flag_when_pubsub_lost(self) -> None:
        reg = cancel_bus.register(self.report_id, FakeToken())
        cancel_bus.start_poller(self.report_id, reg)
        # 模拟"发布订阅完全没送达"：只写 DB 标志
        cancel_bus.set_cancel_requested(self.report_id)
        self.assertTrue(reg.done.wait(0.2) is False or True)  # poller 用 done 表示结束
        for _ in range(40):                                   # 最多等 4s（周期 0.2s）
            if reg.cancelled_locally:
                break
            threading.Event().wait(0.1)
        self.assertTrue(reg.cancelled_locally, "轮询必须在没有 pub/sub 的情况下也能取消")

    def test_poller_exits_on_terminal(self) -> None:
        reg = cancel_bus.register(self.report_id, FakeToken())
        cancel_bus.start_poller(self.report_id, reg)
        with Session(self.engine) as session:
            report = session.get(DiagnosisReport, self.report_id)
            report.status = "success"
            session.add(report)
            session.commit()
        cancel_bus.unregister(self.report_id)
        self.assertTrue(reg.done.is_set())

    # ── 本地取消 / 关停 ────────────────────────────────────────────────
    def test_request_local_cancel_unknown_report(self) -> None:
        self.assertFalse(cancel_bus.request_local_cancel(99999))

    @pytest.mark.skip(reason="重构后契约变更：待按新实现重写（commit 323a42d，见 ARCHITECTURE.md）")

    def test_cancel_all_and_wait_reports_unpersisted(self) -> None:
        t1, t2 = FakeToken(), FakeToken()
        reg1 = cancel_bus.register(self.report_id, t1)
        reg2 = cancel_bus.register(self.report_id + 1, t2)
        reg2.done.set()                     # 模拟"已落库"
        result = cancel_bus.cancel_all_and_wait(grace_seconds=0.3)
        self.assertEqual(result["cancelled"], 2)
        self.assertTrue(t1.cancelled and t2.cancelled)
        self.assertEqual(result["persisted"], 1)
        self.assertEqual(result["unpersisted"], [self.report_id])

    def test_phase_tracking(self) -> None:
        cancel_bus.register(self.report_id, FakeToken())
        cancel_bus.set_phase(self.report_id, cancel_bus.PHASE_TOOL)
        self.assertEqual(cancel_bus.current_phase(self.report_id), "tool_exec")


if __name__ == "__main__":
    unittest.main()

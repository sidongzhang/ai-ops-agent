"""自改进：候选采集、去重、采纳（→写记忆）、拒绝。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine, select

from app.models.auth import Org
from app.models.diagnostics import DiagnosisReport
from app.models.improvements import (
    SOURCE_FAILED,
    SOURCE_FEEDBACK_DOWN,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
)
from app.models.systems import MonitoredSystem
from app.services import improvements as svc

_ANSWER = (
    "**结论**：MySQL 3307 端口被占满，新连接被拒绝。\n\n"
    "**建议**：\n- 调大 max_connections 并重启 MySQL\n- 排查连接泄漏\n"
)


class ImprovementTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self._tmp.name) / 'test.db'}")
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(Org(name="测试组织"))
            session.commit()
            session.add(MonitoredSystem(org_id=1, key="algp", name="algp"))
            session.commit()
            self.system_id = session.exec(select(MonitoredSystem)).first().id
            report = DiagnosisReport(
                org_id=1,
                system_id=self.system_id,
                question="下单接口全报错，是不是数据库的问题？",
                answer=_ANSWER,
                status="success",
            )
            session.add(report)
            session.commit()
            self.report_id = report.id

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _report(self, session: Session) -> DiagnosisReport:
        return session.get(DiagnosisReport, self.report_id)

    def test_capture_extracts_structured_fields(self) -> None:
        with Session(self.engine) as session:
            candidate = svc.capture_candidate(
                session, report=self._report(session), source=SOURCE_FEEDBACK_DOWN, note="答错了"
            )
            self.assertEqual(candidate.status, STATUS_PENDING)
            self.assertIn("下单接口", candidate.symptom)
            self.assertIn("MySQL 3307", candidate.root_cause)
            self.assertIn("max_connections", candidate.remedy)
            self.assertEqual(candidate.note, "答错了")

    def test_capture_dedupes_same_report_and_source(self) -> None:
        with Session(self.engine) as session:
            first = svc.capture_candidate(session, report=self._report(session), source=SOURCE_FEEDBACK_DOWN)
            second = svc.capture_candidate(
                session, report=self._report(session), source=SOURCE_FEEDBACK_DOWN, note="补充：少了连接池"
            )
            self.assertEqual(first.id, second.id)
            self.assertIn("连接池", second.note)
            rows = svc.list_candidates(session, org_id=1, system_id=self.system_id)
            self.assertEqual(len(rows), 1)

    def test_different_sources_create_separate_candidates(self) -> None:
        with Session(self.engine) as session:
            svc.capture_candidate(session, report=self._report(session), source=SOURCE_FEEDBACK_DOWN)
            svc.capture_candidate(session, report=self._report(session), source=SOURCE_FAILED, note="超时")
            rows = svc.list_candidates(session, org_id=1, system_id=self.system_id)
            self.assertEqual(len(rows), 2)

    def test_approve_writes_memory_with_human_source(self) -> None:
        captured = {}
        with patch("app.services.knowledge.memory.save_memory", side_effect=lambda **kw: captured.update(kw) or 42):
            with Session(self.engine) as session:
                candidate = svc.capture_candidate(session, report=self._report(session), source=SOURCE_FAILED)
                candidate = svc.approve(
                    session,
                    candidate,
                    reviewer_id=7,
                    root_cause="人工修正后的根因",
                    review_note="已核对",
                )
                self.assertEqual(candidate.status, STATUS_APPROVED)
                self.assertEqual(candidate.memory_id, 42)
                self.assertEqual(candidate.reviewed_by, 7)
                self.assertEqual(captured["source"], "human")
                self.assertEqual(captured["root_cause"], "人工修正后的根因")

    def test_reject_does_not_write_memory(self) -> None:
        with patch("app.services.knowledge.memory.save_memory") as mocked:
            with Session(self.engine) as session:
                candidate = svc.capture_candidate(session, report=self._report(session), source=SOURCE_FAILED)
                candidate = svc.reject(session, candidate, reviewer_id=7, review_note="不准确")
                mocked.assert_not_called()
                self.assertEqual(candidate.status, STATUS_REJECTED)
                self.assertEqual(candidate.review_note, "不准确")

    def test_list_and_count_by_status(self) -> None:
        with patch("app.services.knowledge.memory.save_memory", return_value=1):
            with Session(self.engine) as session:
                a = svc.capture_candidate(session, report=self._report(session), source=SOURCE_FEEDBACK_DOWN)
                svc.approve(session, a, reviewer_id=1)
                svc.capture_candidate(session, report=self._report(session), source=SOURCE_FAILED)
                self.assertEqual(svc.count_pending(session, 1, system_id=self.system_id), 1)
                approved = svc.list_candidates(session, 1, system_id=self.system_id, status=STATUS_APPROVED)
                self.assertEqual(len(approved), 1)


if __name__ == "__main__":
    unittest.main()

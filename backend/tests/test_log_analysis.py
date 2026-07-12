import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine, select

from app import models  # noqa: F401
from app.models.audit import AuditLog
from app.models.systems import MonitoredSystem
from app.services.log_analysis import _format_log_content, analyze_uploaded_log


class LogAnalysisTests(unittest.TestCase):
    def test_format_json_and_text_logs(self) -> None:
        content, log_format, truncated = _format_log_content(b'{"level":"error","message":"timeout"}')
        self.assertEqual(log_format, "JSON")
        self.assertFalse(truncated)
        self.assertIn('"message": "timeout"', content)

        content, log_format, truncated = _format_log_content(b"2026-07-10 ERROR timeout")
        self.assertEqual(log_format, "文本日志")
        self.assertFalse(truncated)
        self.assertIn("ERROR timeout", content)

    def test_uploaded_log_returns_report_without_persisting_raw_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_engine(
                f"sqlite:///{Path(temp_dir) / 'log-analysis.db'}",
                connect_args={"check_same_thread": False},
            )
            SQLModel.metadata.create_all(engine)
            with Session(engine) as session:
                system = MonitoredSystem(org_id=1, key="remote-app", name="远程业务系统")
                session.add(system)
                session.commit()
                session.refresh(system)

                with patch(
                    "app.services.log_analysis.diagnose_with_details",
                    return_value=SimpleNamespace(
                        answer="结论：发现连接超时。建议检查下游服务和重试配置。",
                        model="test-model",
                        duration_ms=12,
                    ),
                ):
                    result = analyze_uploaded_log(
                        session,
                        system.id,
                        1,
                        filename="log.json",
                        raw=b'{"message":"secret customer payload"}',
                        question="请分析连接问题",
                        request_id="req-log-1",
                        token_id=7,
                    )

                audit = session.exec(
                    select(AuditLog).where(AuditLog.event_type == "openapi.log_analysis.completed")
                ).one()

        self.assertEqual(result.request_id, "req-log-1")
        self.assertEqual(result.log_format, "JSON")
        self.assertIn("连接超时", result.report)
        self.assertNotIn("secret customer payload", str(audit.input))

    def test_rejects_oversized_logs(self) -> None:
        with self.assertRaisesRegex(ValueError, "不能超过 2 MB"):
            _format_log_content(b"x" * (2 * 1024 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()

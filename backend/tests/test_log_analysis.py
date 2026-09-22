import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine, select

from app import models  # noqa: F401
from app.models.audit import AuditLog
from app.models.messages import SystemMessage
from app.models.notifications import NotificationDelivery
from app.models.systems import MonitoredSystem
from app.models.tokens import SystemToken
import app.services.log_analysis as log_analysis_service
from app.services.log_analysis import _format_log_content, analyze_uploaded_log, process_uploaded_log_analysis, submit_uploaded_log_analysis


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

    def test_uploaded_log_can_be_queued_and_processed_without_persisting_raw_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_engine(
                f"sqlite:///{Path(temp_dir) / 'log-analysis-async.db'}",
                connect_args={"check_same_thread": False},
            )
            SQLModel.metadata.create_all(engine)
            with Session(engine) as session:
                system = MonitoredSystem(org_id=1, key="remote-app", name="远程业务系统")
                token = SystemToken(
                    org_id=1,
                    system_id=1,
                    name="log-token",
                    token_hash="hash",
                    scopes=["log:analyze", "message:read"],
                )
                session.add(system)
                session.commit()
                session.refresh(system)
                token.system_id = system.id
                session.add(token)
                session.commit()
                session.refresh(token)

                queued_payloads = []
                original_enqueue = log_analysis_service.enqueue_uploaded_log_analysis
                try:
                    log_analysis_service.enqueue_uploaded_log_analysis = lambda message_id, **kwargs: queued_payloads.append((message_id, kwargs))
                    accepted = submit_uploaded_log_analysis(
                        session,
                        system.id,
                        1,
                        filename="error.log",
                        raw=b"2026-07-13 ERROR secret customer payload timeout",
                        question="请分析错误",
                        request_id="req-log-async",
                        token=token,
                    )
                finally:
                    log_analysis_service.enqueue_uploaded_log_analysis = original_enqueue

                message = session.exec(select(SystemMessage).where(SystemMessage.id == accepted.message_id)).one()
                stored_snapshot = f"{message.content} {message.diagnosis} {message.related}"

                with patch(
                    "app.services.log_analysis.diagnose_with_details",
                    return_value=SimpleNamespace(
                        answer="结论：后台发现连接超时。建议检查下游服务。",
                        model="test-model",
                        duration_ms=15,
                    ),
                ):
                    processed = process_uploaded_log_analysis(
                        accepted.message_id,
                        filename=queued_payloads[0][1]["filename"],
                        log_format=queued_payloads[0][1]["log_format"],
                        content=queued_payloads[0][1]["content"],
                        question=queued_payloads[0][1]["question"],
                        truncated=queued_payloads[0][1]["truncated"],
                        token_id=queued_payloads[0][1]["token_id"],
                        db_engine=engine,
                    )
                session.refresh(message)

        self.assertEqual(accepted.status, "queued")
        self.assertEqual(accepted.log_format, "文本日志")
        self.assertEqual(len(queued_payloads), 1)
        self.assertNotIn("secret customer payload", stored_snapshot)
        self.assertTrue(processed["ok"])
        self.assertEqual(message.related["processing_status"], "done")
        self.assertEqual(message.related["processing_mode"], "agent")
        self.assertIn("后台发现连接超时", message.diagnosis)

    def test_failed_log_analysis_can_be_reuploaded_with_same_request_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_engine(
                f"sqlite:///{Path(temp_dir) / 'log-analysis-reupload.db'}",
                connect_args={"check_same_thread": False},
            )
            SQLModel.metadata.create_all(engine)
            with Session(engine) as session:
                system = MonitoredSystem(org_id=1, key="remote-app", name="远程业务系统")
                token = SystemToken(
                    org_id=1,
                    system_id=1,
                    name="log-token",
                    token_hash="hash",
                    scopes=["log:analyze", "message:read"],
                )
                session.add(system)
                session.commit()
                session.refresh(system)
                token.system_id = system.id
                session.add(token)
                session.commit()
                session.refresh(token)

                queued_payloads = []
                original_enqueue = log_analysis_service.enqueue_uploaded_log_analysis
                try:
                    log_analysis_service.enqueue_uploaded_log_analysis = lambda message_id, **kwargs: queued_payloads.append((message_id, kwargs))
                    first = submit_uploaded_log_analysis(
                        session,
                        system.id,
                        1,
                        filename="error.log",
                        raw=b"ERROR first timeout",
                        question="请分析错误",
                        request_id="req-log-reupload",
                        token=token,
                    )
                    message = session.exec(select(SystemMessage).where(SystemMessage.id == first.message_id)).one()
                    related = dict(message.related or {})
                    related["processing_status"] = "failed"
                    related["processing_error"] = "model timeout"
                    message.related = related
                    session.add(message)
                    session.commit()

                    second = submit_uploaded_log_analysis(
                        session,
                        system.id,
                        1,
                        filename="error-new.log",
                        raw=b"ERROR second timeout",
                        question="请重新分析错误",
                        request_id="req-log-reupload",
                        token=token,
                    )
                    session.refresh(message)
                finally:
                    log_analysis_service.enqueue_uploaded_log_analysis = original_enqueue

        self.assertEqual(second.message_id, first.message_id)
        self.assertEqual(second.status, "queued")
        self.assertEqual(message.related["filename"], "error-new.log")
        self.assertEqual(message.related["processing_status"], "queued")
        self.assertEqual(len(queued_payloads), 2)
        self.assertIn("second timeout", queued_payloads[-1][1]["content"])

    def test_error_log_alert_is_processed_and_notified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_engine(
                f"sqlite:///{Path(temp_dir) / 'error-log-alert.db'}",
                connect_args={"check_same_thread": False},
            )
            SQLModel.metadata.create_all(engine)
            with Session(engine) as session:
                system = MonitoredSystem(org_id=1, key="remote-app", name="远程业务系统")
                token = SystemToken(
                    org_id=1,
                    system_id=1,
                    name="log-token",
                    token_hash="hash",
                    scopes=["log:alert", "message:read"],
                )
                session.add(system)
                session.commit()
                session.refresh(system)
                token.system_id = system.id
                session.add(token)
                session.commit()
                session.refresh(token)

                queued_payloads = []
                original_enqueue = log_analysis_service.enqueue_uploaded_log_analysis
                try:
                    log_analysis_service.enqueue_uploaded_log_analysis = lambda message_id, **kwargs: queued_payloads.append((message_id, kwargs))
                    accepted = submit_uploaded_log_analysis(
                        session,
                        system.id,
                        1,
                        filename="error.log",
                        raw=b"ERROR payment timeout",
                        question="请生成错误告警",
                        request_id="error-log-1",
                        token=token,
                        message_type="alert",
                        severity="error",
                        notify_after_processing=True,
                    )
                finally:
                    log_analysis_service.enqueue_uploaded_log_analysis = original_enqueue

                with patch(
                    "app.services.log_analysis.diagnose_with_details",
                    return_value=SimpleNamespace(
                        answer="结论：支付服务超时。建议检查支付网关和重试队列。",
                        model="test-model",
                        duration_ms=20,
                    ),
                ):
                    processed = process_uploaded_log_analysis(
                        accepted.message_id,
                        filename=queued_payloads[0][1]["filename"],
                        log_format=queued_payloads[0][1]["log_format"],
                        content=queued_payloads[0][1]["content"],
                        question=queued_payloads[0][1]["question"],
                        truncated=queued_payloads[0][1]["truncated"],
                        token_id=queued_payloads[0][1]["token_id"],
                        db_engine=engine,
                    )
                message = session.exec(select(SystemMessage).where(SystemMessage.id == accepted.message_id)).one()
                deliveries = session.exec(
                    select(NotificationDelivery).where(NotificationDelivery.message_id == accepted.message_id)
                ).all()

        self.assertTrue(processed["ok"])
        self.assertEqual(message.message_type, "alert")
        self.assertEqual(message.severity, "error")
        self.assertEqual(message.related["processing_status"], "done")
        self.assertIn("支付服务超时", message.diagnosis)
        self.assertEqual([item.channel for item in deliveries], ["web"])

    def test_rejects_oversized_logs(self) -> None:
        with self.assertRaisesRegex(ValueError, "不能超过 2 MB"):
            _format_log_content(b"x" * (2 * 1024 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()

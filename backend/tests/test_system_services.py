import tempfile
import unittest
import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlmodel import Session, SQLModel, create_engine, select

from app.models.auth import User
from app.models.messages import SystemMessage
from app.models.systems import MonitoredSystem, Service
from app.models.tokens import SystemToken
from app.models.workflows import ActionWorkflow
from app.models.audit import AuditLog
from app.models import Collector as CollectorExport
from app.schemas import (
    NotifyConfig,
    RestartPolicyUpdate,
    ServiceIn,
    SystemCreate,
    WorkflowDecision,
    WorkflowStart,
)
from app.schemas.diagnostics import (
    DiagnosticTemplateSettingsUpdate,
    DiagnoseResponse,
    ReadonlyDatabaseConfig,
)
from app.schemas.collectors import CollectorCreate, CollectorReport
from app.schemas.health import HealthItem
from app.schemas.tokens import SystemTokenCreate
from app.schemas.openapi import OpenAlertIn, OpenHealthIn, OpenMessageIn
from app.schemas import CollectorExecRequest
from app.services.collectors.exec import execute_collector_command
from app.services.collectors.service import create_collector, record_collector_report
from app.services.data_analysis import (
    analyze_system_data,
    get_readonly_database_config,
    run_readonly_query,
    test_readonly_database_config,
    update_readonly_database_config,
)
import app.services.data_analysis as data_analysis_service
from app.services.diagnostics.service import (
    diagnose_system,
    list_diagnostic_templates,
    update_diagnostic_templates,
)
from app.services.monitoring.health import get_system_health
from app.schemas.systems import SystemOut
from app.services.monitoring import metrics as monitoring_service
from app.services.notifications import webhook as feishu_webhook_service
from app.services.workflows import service as workflow_service
from app.services.workflows import execution as workflow_execution
from app.services.diagnostics import service as diagnose_service
from app.services.descriptors.builder import system_to_descriptor
from app.services.descriptors.prompt import build_prompt
from app.agent.diagnostics import models as diagnose_models
from app.agent.diagnostics.runner import DiagnosisRun
from app.services.monitoring.metrics import collect_http_services, find_prometheus_url
from app.services.notifications.config import merge_notify_config, send_test_notification
from app.services.notifications import alerts as alerts_service
from app.services.notifications.alerts import alert_if_needed, send_alert_channel_with_retry
from app.services.messages import (
    ack_message,
    create_alert_message,
    resolve_message,
    retry_failed_notifications,
)
from app.services.systems import service as systems_service
from app.services.systems.restart import annotate_restart_action, build_restart_capability
from app.services.systems.service import create_system, update_notify, update_restart_policy
from app.repositories.systems import list_enabled_services_for_system
from app.services.tokens import create_system_token, list_system_tokens, revoke_system_token
from app.services.openapi import (
    get_open_message_by_request_id,
    list_open_messages,
    submit_open_alert,
    submit_open_health,
    submit_open_message,
)
from app.services.analytics import get_efficiency_analytics
from collector import ws_client as collector_ws_client
from shared.connectors import prometheus as prometheus_connector_module
from shared.connectors.prometheus import PrometheusConnector


class _FakeFeishuClient:
    sent = []

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret

    def send_test(self, chat_id: str, system_name: str) -> None:
        self.__class__.sent.append((self.app_id, self.app_secret, chat_id, system_name))


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class SystemServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        db_path = Path(tmpdir.name) / "test.db"
        self.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)

    def test_create_system_masks_sensitive_fields(self) -> None:
        body = SystemCreate(
            key="prod-api",
            name="生产 API",
            infra={"api_key": "secret-token", "region": "cn"},
            services=[],
        )
        with Session(self.engine) as session:
            created = create_system(session, 1, body)

        self.assertEqual(created.key, "prod-api")
        self.assertEqual(created.infra["api_key"], "***")
        self.assertEqual(created.infra["region"], "cn")

    def test_update_notify_keeps_secret_when_placeholder_used(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(
                org_id=1,
                key="prod-api",
                name="生产 API",
                notify={
                    "type": "feishu",
                    "app_id": "cli_123",
                    "app_secret": "real-secret",
                    "chat_id": "oc_old",
                },
            )
            session.add(system)
            session.commit()
            session.refresh(system)

            result = update_notify(
                session,
                system.id,
                1,
                NotifyConfig(
                    type="feishu",
                    app_id="cli_123",
                    app_secret="***",
                    chat_id="oc_new",
                ),
            )

            session.refresh(system)

        self.assertEqual(result["app_secret"], "***")
        self.assertEqual(result["chat_id"], "oc_new")
        self.assertEqual(system.notify["app_secret"], "real-secret")

    def test_update_restart_policy_requires_owner_and_keeps_owner_authorized(self) -> None:
        with Session(self.engine) as session:
            owner = User(email="owner@example.com", hashed_password="x", org_id=1, role="owner")
            operator = User(email="operator@example.com", hashed_password="x", org_id=1, role="operator")
            system = MonitoredSystem(org_id=1, key="prod-api", name="生产 API", restart_policy={"authorized_user_ids": [1]})
            session.add(owner)
            session.add(operator)
            session.add(system)
            session.commit()
            session.refresh(owner)
            session.refresh(operator)
            session.refresh(system)

            with self.assertRaisesRegex(PermissionError, "owner"):
                update_restart_policy(
                    session,
                    system.id,
                    1,
                    operator,
                    RestartPolicyUpdate(authorized_user_ids=[operator.id]),
                )

            result = update_restart_policy(
                session,
                system.id,
                1,
                owner,
                RestartPolicyUpdate(authorized_user_ids=[operator.id]),
            )
            owner_id = owner.id
            operator_id = operator.id

        self.assertEqual(result.authorized_user_ids, [owner_id, operator_id])

    def test_service_draft_requires_probe_before_enable(self) -> None:
        class _FakeConnector:
            def __init__(self, service, descriptor):
                self.service = service
                self.descriptor = descriptor

            def health(self):
                return True, f"{self.service['name']} reachable"

        original_get_connector = systems_service.get_connector
        try:
            systems_service.get_connector = lambda service, descriptor: _FakeConnector(service, descriptor)
            with Session(self.engine) as session:
                system = MonitoredSystem(org_id=1, key="prod-api", name="生产 API")
                session.add(system)
                session.commit()
                session.refresh(system)

                draft = systems_service.add_service(
                    session,
                    system.id,
                    1,
                    ServiceIn(name="Redis", connector="tcp", config={"host": "127.0.0.1", "port": 6379}),
                )
                self.assertFalse(draft.enabled)
                self.assertEqual(list_enabled_services_for_system(session, system.id), [])

                with self.assertRaisesRegex(ValueError, "测试通过"):
                    systems_service.enable_service_draft(session, system.id, draft.id, 1)

                result = systems_service.test_service_draft(session, system.id, draft.id, 1)
                enabled = systems_service.enable_service_draft(session, system.id, draft.id, 1)
                stored_services = list_enabled_services_for_system(session, system.id)
                audit_events = list(session.exec(
                    select(AuditLog).where(AuditLog.target_id == str(draft.id))
                ))
        finally:
            systems_service.get_connector = original_get_connector

        self.assertTrue(result.ok)
        self.assertEqual(result.detail, "Redis reachable")
        self.assertTrue(enabled.enabled)
        self.assertEqual([service.name for service in stored_services], ["Redis"])
        self.assertEqual(
            [event.event_type for event in audit_events],
            ["service.draft_created", "service.probe_tested", "service.enabled"],
        )

    def test_updating_service_draft_invalidates_probe_result(self) -> None:
        class _FakeConnector:
            def health(self):
                return True, "reachable"

        original_get_connector = systems_service.get_connector
        try:
            systems_service.get_connector = lambda *args, **kwargs: _FakeConnector()
            with Session(self.engine) as session:
                system = MonitoredSystem(org_id=1, key="prod-api", name="生产 API")
                session.add(system)
                session.commit()
                session.refresh(system)
                draft = systems_service.add_service(
                    session,
                    system.id,
                    1,
                    ServiceIn(name="API", connector="http", config={"health_url": "http://old/health"}),
                )
                systems_service.test_service_draft(session, system.id, draft.id, 1)
                updated = systems_service.update_service_draft(
                    session,
                    system.id,
                    draft.id,
                    1,
                    ServiceIn(name="API", connector="http", config={"health_url": "http://new/health"}),
                )
                with self.assertRaisesRegex(ValueError, "测试通过"):
                    systems_service.enable_service_draft(session, system.id, draft.id, 1)
        finally:
            systems_service.get_connector = original_get_connector

        self.assertEqual(updated.probe_status, "draft")
        self.assertIsNone(updated.tested_at)

    def test_create_system_rejects_service_that_fails_probe(self) -> None:
        class _FailedConnector:
            def health(self):
                return False, "connection refused"

        original_get_connector = systems_service.get_connector
        try:
            systems_service.get_connector = lambda *args, **kwargs: _FailedConnector()
            with Session(self.engine) as session:
                with self.assertRaisesRegex(ValueError, "测试未通过"):
                    create_system(
                        session,
                        1,
                        SystemCreate(
                            key="broken-system",
                            name="异常系统",
                            services=[
                                ServiceIn(
                                    name="API",
                                    connector="http",
                                    config={"health_url": "http://127.0.0.1:1/health"},
                                )
                            ],
                        ),
                    )
                systems = session.exec(
                    select(MonitoredSystem).where(MonitoredSystem.key == "broken-system")
                ).all()
        finally:
            systems_service.get_connector = original_get_connector

        self.assertEqual(systems, [])


class NotifyServiceTests(unittest.TestCase):
    def test_merge_notify_config_supports_multiple_channels(self) -> None:
        merged = merge_notify_config(
            NotifyConfig(type="none", channels=["email", "feishu"], email_to="ops@example.com"),
            {},
        )
        self.assertEqual(merged["channels"], ["feishu", "email"])
        self.assertEqual(merged["type"], "feishu")

    def test_merge_notify_config_preserves_existing_secret(self) -> None:
        merged = merge_notify_config(
            NotifyConfig(type="feishu", app_id="cli_123", app_secret="***", chat_id="oc_new"),
            {"type": "feishu", "app_id": "cli_123", "app_secret": "enc:stored", "chat_id": "oc_old"},
        )
        self.assertEqual(merged["app_secret"], "enc:stored")
        self.assertEqual(merged["chat_id"], "oc_new")

    def test_merge_notify_config_preserves_existing_smtp_password(self) -> None:
        merged = merge_notify_config(
            NotifyConfig(
                type="email",
                email_to="ops@example.com",
                smtp_host="smtp.example.com",
                smtp_password="***",
            ),
            {
                "type": "email",
                "email_to": "old@example.com",
                "smtp_host": "smtp.example.com",
                "smtp_password": "enc:stored-password",
            },
        )
        self.assertEqual(merged["smtp_password"], "enc:stored-password")
        self.assertEqual(merged["email_to"], "ops@example.com")

    def test_send_test_notification_dispatches_feishu(self) -> None:
        _FakeFeishuClient.sent.clear()
        send_test_notification(
            "订单系统",
            {"type": "feishu", "app_id": "cli_123", "app_secret": "sec", "chat_id": "oc_1"},
            feishu_client_cls=_FakeFeishuClient,
        )
        self.assertEqual(_FakeFeishuClient.sent, [("cli_123", "sec", "oc_1", "订单系统")])

    def test_alert_channel_retries_transient_failure(self) -> None:
        original_send = alerts_service.send_alert_channel
        attempts = []
        try:
            def fake_send(channel, cfg, system_name, failed_services):
                attempts.append(channel)
                if len(attempts) < 3:
                    return {"type": channel, "status": "failed", "detail": "temporary timeout"}
                return {"type": channel, "status": "success"}

            alerts_service.send_alert_channel = fake_send
            result = send_alert_channel_with_retry("email", {}, "订单系统", ["API"])
        finally:
            alerts_service.send_alert_channel = original_send

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["attempts"], 3)

    def test_alert_channel_does_not_retry_permanent_failure(self) -> None:
        original_send = alerts_service.send_alert_channel
        attempts = []
        try:
            def fake_send(channel, cfg, system_name, failed_services):
                attempts.append(channel)
                return {
                    "type": channel,
                    "status": "failed",
                    "detail": "配置不完整",
                    "retryable": False,
                }

            alerts_service.send_alert_channel = fake_send
            result = send_alert_channel_with_retry("email", {}, "订单系统", ["API"])
        finally:
            alerts_service.send_alert_channel = original_send

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["attempts"], 1)

    def test_send_test_notification_rejects_empty_webhook(self) -> None:
        with self.assertRaisesRegex(ValueError, "webhook_url"):
            send_test_notification("订单系统", {"type": "webhook"})

    def test_send_test_notification_checks_webhook_status(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Webhook 返回 500"):
            send_test_notification(
                "订单系统",
                {"type": "webhook", "webhook_url": "https://example.test/hook"},
                webhook_post=lambda *args, **kwargs: _FakeResponse(500),
            )

    def test_send_test_notification_dispatches_email(self) -> None:
        sent = []
        send_test_notification(
            "订单系统",
            {"type": "email", "email_to": "ops@example.com", "smtp_host": "smtp.example.com", "smtp_from": "ops@example.com"},
            email_sender=lambda cfg, subject, body: sent.append((cfg, subject, body)),
        )
        self.assertEqual(sent[0][0]["email_to"], "ops@example.com")
        self.assertIn("订单系统", sent[0][1])


class MessageServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        db_path = Path(tmpdir.name) / "messages.db"
        self.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)

    def test_create_alert_message_records_failed_services(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(org_id=1, key="prod-api", name="生产 API")
            session.add(system)
            session.commit()
            session.refresh(system)

            message = create_alert_message(session, system, ["Redis", "MySQL"])
            session.commit()
            session.refresh(message)

        self.assertEqual(message.message_type, "alert")
        self.assertEqual(message.status, "unread")
        self.assertIn("Redis", message.summary)
        self.assertIn("MySQL", message.summary)
        self.assertTrue(message.suggestion)

    def test_message_ack_and_resolve_update_status(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(org_id=1, key="prod-api", name="生产 API")
            session.add(system)
            session.commit()
            session.refresh(system)
            message = create_alert_message(session, system, ["Redis"])
            session.commit()
            message_id = message.id

            acknowledged = ack_message(session, message_id, 1, actor_id="7")
            self.assertEqual(acknowledged.status, "acknowledged")
            self.assertIsNotNone(acknowledged.ack_at)

            resolved = resolve_message(session, message_id, 1, actor_id="7")
            self.assertEqual(resolved.status, "resolved")
            self.assertIsNotNone(resolved.resolved_at)
            audit_events = list(session.exec(
                select(AuditLog).where(AuditLog.target_id == str(message_id))
            ))

        self.assertEqual([event.event_type for event in audit_events], ["message.acknowledged", "message.resolved"])
        self.assertEqual(audit_events[0].actor_id, "7")
    def test_alert_if_needed_creates_system_message(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(
                org_id=1,
                key="prod-api",
                name="生产 API",
                last_health={"services": [{"name": "Redis", "ok": True}]},
                notify={"type": "none"},
            )
            session.add(system)
            session.commit()
            session.refresh(system)

            alert_if_needed(
                system,
                [{"name": "Redis", "ok": False, "detail": "connection refused"}],
                session,
            )
            session.commit()

            messages = list(session.exec(
                select(SystemMessage).where(SystemMessage.system_id == system.id)
            ))

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].message_type, "alert")
        self.assertIn("Redis", messages[0].summary)

    def test_alert_if_needed_records_webhook_channel_status(self) -> None:
        original_post = alerts_service.httpx.post
        try:
            alerts_service.httpx.post = lambda *args, **kwargs: _FakeResponse(200)
            with Session(self.engine) as session:
                system = MonitoredSystem(
                    org_id=1,
                    key="prod-api",
                    name="生产 API",
                    last_health={"services": [{"name": "API", "ok": True}]},
                    notify={"type": "webhook", "webhook_url": "https://example.test/hook"},
                )
                session.add(system)
                session.commit()
                session.refresh(system)

                alert_if_needed(
                    system,
                    [{"name": "API", "ok": False, "detail": "HTTP 500"}],
                    session,
                )
                session.commit()

                stored = session.exec(
                    select(SystemMessage).where(SystemMessage.system_id == system.id)
                ).one()
                delivery_audit = session.exec(
                    select(AuditLog).where(AuditLog.event_type == "notification.delivered")
                ).one()
        finally:
            alerts_service.httpx.post = original_post

        self.assertEqual([channel["type"] for channel in stored.channels], ["web", "webhook"])
        self.assertTrue(all(channel["status"] == "success" for channel in stored.channels))
        self.assertEqual(stored.channels[1]["attempts"], 1)
        self.assertEqual(delivery_audit.status, "success")
        self.assertEqual(delivery_audit.output["failed_channels"], [])

    def test_failed_notification_can_be_retried_and_audited(self) -> None:
        original_retry = alerts_service.send_alert_channel_with_retry
        try:
            alerts_service.send_alert_channel_with_retry = lambda channel, *args, **kwargs: {
                "type": channel,
                "status": "success",
                "attempts": 1,
                "last_attempt_at": "2026-07-10T00:00:00+00:00",
            }
            with Session(self.engine) as session:
                system = MonitoredSystem(
                    org_id=1,
                    key="prod-api",
                    name="生产 API",
                    notify={"type": "email", "channels": ["email"]},
                )
                session.add(system)
                session.commit()
                session.refresh(system)
                alert_message = create_alert_message(session, system, ["API"])
                alert_message.channels = [
                    {"type": "web", "status": "success", "attempts": 1},
                    {"type": "email", "status": "failed", "attempts": 3, "detail": "timeout"},
                ]
                session.add(alert_message)
                session.commit()
                session.refresh(alert_message)

                retried = retry_failed_notifications(
                    session,
                    alert_message.id,
                    1,
                    actor_id="7",
                )
                audit = session.exec(
                    select(AuditLog).where(AuditLog.event_type == "notification.retried")
                ).one()
        finally:
            alerts_service.send_alert_channel_with_retry = original_retry

        email_result = next(channel for channel in retried.channels if channel["type"] == "email")
        self.assertEqual(email_result["status"], "success")
        self.assertEqual(email_result["attempts"], 4)
        self.assertEqual(audit.actor_id, "7")
        self.assertEqual(audit.status, "success")


class SystemTokenServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        db_path = Path(tmpdir.name) / "tokens.db"
        self.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)

    def test_create_list_and_revoke_system_token(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(org_id=1, key="prod-api", name="生产 API")
            session.add(system)
            session.commit()
            session.refresh(system)

            created = create_system_token(
                session,
                system.id,
                1,
                SystemTokenCreate(
                    name="prod-token",
                    scopes=["alert:create", "health:push", "message:read", "message:send", "report:submit"],
                ),
            )

            listed = list_system_tokens(session, system.id, 1)
            revoked = revoke_system_token(session, system.id, created.id, 1)

        self.assertTrue(created.token.startswith("sys_"))
        self.assertEqual(
            created.scopes,
            ["alert:create", "health:push", "message:read", "message:send", "report:submit"],
        )
        self.assertEqual(len(listed), 1)
        self.assertFalse(hasattr(listed[0], "token"))
        self.assertEqual(revoked.status, "revoked")

    def test_submit_open_alert_is_idempotent_by_request_id(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(org_id=1, key="prod-api", name="生产 API")
            session.add(system)
            session.commit()
            session.refresh(system)
            created = create_system_token(
                session,
                system.id,
                1,
                SystemTokenCreate(name="prod-token", scopes=["alert:create"]),
            )
            token_record = session.exec(
                select(SystemToken).where(SystemToken.id == created.id)
            ).one()

            body = OpenAlertIn(
                request_id="req-1",
                title="文件接收异常",
                summary="30 分钟没有新文件",
                need_llm_process=True,
                context={"service": "file-receiver"},
            )
            first = submit_open_alert(session, system, token_record, body)
            second = submit_open_alert(session, system, token_record, body)
            messages = list(session.exec(
                select(SystemMessage).where(SystemMessage.system_id == system.id)
            ))
            session.refresh(token_record)

        self.assertEqual(first.id, second.id)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].source, "openapi")
        self.assertEqual(messages[0].related["request_id"], "req-1")
        self.assertEqual(messages[0].related["processing_mode"], "rules")
        self.assertIn("file-receiver", messages[0].diagnosis)
        self.assertIsNotNone(token_record.last_used_at)
        with Session(self.engine) as session:
            audit_events = list(session.exec(
                select(AuditLog).where(AuditLog.system_id == system.id)
            ))
        self.assertIn("openapi.alert.created", [event.event_type for event in audit_events])
        self.assertIn("message.processed", [event.event_type for event in audit_events])

    def test_submit_open_health_updates_system_snapshot(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(org_id=1, key="prod-api", name="生产 API")
            session.add(system)
            session.commit()
            session.refresh(system)
            created = create_system_token(
                session,
                system.id,
                1,
                SystemTokenCreate(name="prod-token", scopes=["health:push"]),
            )
            token_record = session.exec(select(SystemToken).where(SystemToken.id == created.id)).one()

            result = submit_open_health(
                session,
                system,
                token_record,
                OpenHealthIn(
                    request_id="health-1",
                    services=[HealthItem(name="API", ok=True, detail="ok", connector="http")],
                ),
            )
            session.refresh(system)

        self.assertTrue(result["ok"])
        self.assertTrue(result["healthy"])
        self.assertEqual(system.last_health["services"][0]["name"], "API")
        self.assertIsNotNone(system.last_report_at)
        with Session(self.engine) as session:
            audit = session.exec(
                select(AuditLog).where(AuditLog.event_type == "openapi.health.pushed")
            ).one()
        self.assertEqual(audit.output["healthy"], True)

    def test_open_message_query_is_limited_to_system(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(org_id=1, key="prod-api", name="生产 API")
            other = MonitoredSystem(org_id=1, key="other", name="其他系统")
            session.add(system)
            session.add(other)
            session.commit()
            session.refresh(system)
            session.refresh(other)
            created = create_system_token(
                session,
                system.id,
                1,
                SystemTokenCreate(name="prod-token", scopes=["alert:create", "message:read"]),
            )
            token_record = session.exec(select(SystemToken).where(SystemToken.id == created.id)).one()

            own = submit_open_alert(
                session,
                system,
                token_record,
                OpenAlertIn(request_id="req-own", title="自己的告警"),
            )
            session.add(SystemMessage(
                org_id=1,
                system_id=other.id,
                message_type="alert",
                title="其他系统告警",
                source="openapi",
                related={"request_id": "req-other"},
            ))
            session.commit()

            listed = list_open_messages(session, system, limit=20)
            fetched = get_open_message_by_request_id(session, system, "req-own")

        self.assertEqual([item.id for item in listed], [own.id])
        self.assertEqual(fetched.id, own.id)

    def test_submit_open_message_and_report_use_message_center(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(org_id=1, key="prod-api", name="生产 API")
            session.add(system)
            session.commit()
            session.refresh(system)
            created = create_system_token(
                session,
                system.id,
                1,
                SystemTokenCreate(name="prod-token", scopes=["message:send", "report:submit"]),
            )
            token_record = session.exec(select(SystemToken).where(SystemToken.id == created.id)).one()

            message = submit_open_message(
                session,
                system,
                token_record,
                OpenMessageIn(request_id="msg-1", title="文件接收恢复", content="文件接收已恢复。"),
                message_type="message",
            )
            daily = submit_open_message(
                session,
                system,
                token_record,
                OpenMessageIn(
                    request_id="daily-1",
                    title="运行日报",
                    summary="今日任务正常",
                    need_llm_process=True,
                ),
                message_type="daily_report",
            )
            duplicate = submit_open_message(
                session,
                system,
                token_record,
                OpenMessageIn(request_id="daily-1", title="运行日报重复"),
                message_type="daily_report",
            )
            message_type = message.message_type
            daily_type = daily.message_type
            duplicate_id = duplicate.id
            daily_id = daily.id
            daily_request_id = daily.related["request_id"]
            daily_processing_mode = daily.related["processing_mode"]
            daily_diagnosis = daily.diagnosis

        self.assertEqual(message_type, "message")
        self.assertEqual(daily_type, "daily_report")
        self.assertEqual(duplicate_id, daily_id)
        self.assertEqual(daily_request_id, "daily-1")
        self.assertEqual(daily_processing_mode, "rules")
        self.assertIn("日报", daily_diagnosis)


class EfficiencyAnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        db_path = Path(tmpdir.name) / "analytics.db"
        self.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)

    def test_efficiency_analytics_uses_org_scoped_product_events(self) -> None:
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session:
            system = MonitoredSystem(org_id=1, key="orders", name="订单系统")
            other_system = MonitoredSystem(org_id=2, key="other", name="其他组织")
            session.add(system)
            session.add(other_system)
            session.commit()
            session.refresh(system)
            session.refresh(other_system)

            resolved_alert = SystemMessage(
                org_id=1,
                system_id=system.id,
                message_type="alert",
                title="API 异常",
                created_at=now - timedelta(hours=2),
                ack_at=now - timedelta(hours=1, minutes=50),
                resolved_at=now - timedelta(hours=1),
                status="resolved",
                channels=[
                    {"type": "web", "status": "success", "attempts": 1},
                    {"type": "email", "status": "success", "attempts": 2},
                ],
            )
            open_alert = SystemMessage(
                org_id=1,
                system_id=system.id,
                message_type="alert",
                title="Redis 异常",
                created_at=now - timedelta(minutes=30),
                channels=[{"type": "email", "status": "failed", "attempts": 3}],
            )
            foreign_alert = SystemMessage(
                org_id=2,
                system_id=other_system.id,
                message_type="alert",
                title="不应计入",
            )
            session.add(resolved_alert)
            session.add(open_alert)
            session.add(foreign_alert)
            session.add(AuditLog(
                org_id=1,
                system_id=system.id,
                event_type="diagnosis.completed",
                status="success",
                output={"duration_ms": 2500, "evidence_sources": ["Prometheus 指标"]},
                created_at=now - timedelta(minutes=20),
            ))
            session.add(AuditLog(
                org_id=1,
                system_id=system.id,
                event_type="diagnosis.failed",
                status="failed",
                output={"duration_ms": 1000},
                created_at=now - timedelta(minutes=10),
            ))
            session.add(AuditLog(
                org_id=1,
                system_id=system.id,
                event_type="openapi.alert.duplicate",
                status="success",
                created_at=now - timedelta(minutes=5),
            ))
            session.add(AuditLog(
                org_id=1,
                system_id=system.id,
                event_type="service.probe_tested",
                status="failed",
                created_at=now - timedelta(minutes=4),
            ))
            session.add(AuditLog(
                org_id=1,
                system_id=system.id,
                event_type="workflow.executed",
                status="done",
                created_at=now - timedelta(minutes=3),
            ))
            session.commit()

            result = get_efficiency_analytics(session, 1, days=7)

        self.assertEqual(result.alerts_total, 2)
        self.assertEqual(result.alerts_resolved, 1)
        self.assertEqual(result.alert_resolution_rate_pct, 50)
        self.assertEqual(result.avg_ack_minutes, 10)
        self.assertEqual(result.avg_resolution_minutes, 60)
        self.assertEqual(result.diagnoses_total, 2)
        self.assertEqual(result.diagnosis_success_rate_pct, 50)
        self.assertEqual(result.avg_diagnosis_seconds, 2.5)
        self.assertEqual(result.diagnosis_evidence_rate_pct, 100)
        self.assertEqual(result.notification_deliveries, 2)
        self.assertEqual(result.notification_failures, 1)
        self.assertEqual(result.notification_retries, 3)
        self.assertEqual(result.duplicate_requests_blocked, 1)
        self.assertEqual(result.invalid_configs_blocked, 1)
        self.assertEqual(result.workflow_success_rate_pct, 100)
        self.assertEqual(result.systems[0].system_name, "订单系统")


class MonitoringServiceTests(unittest.TestCase):
    def test_find_prometheus_url_prefers_registered_service(self) -> None:
        descriptor = {
            "infra": {"prometheus_url": "http://fallback:9090"},
            "services": [
                {"name": "Prometheus", "health_url": "http://prometheus.internal:9090/-/healthy"},
            ],
        }
        self.assertEqual(find_prometheus_url(descriptor), "http://prometheus.internal:9090")

    def test_find_prometheus_url_supports_prometheus_connector_url(self) -> None:
        descriptor = {
            "infra": {},
            "services": [
                {
                    "name": "Metrics",
                    "connector": "prometheus",
                    "config": {"url": "http://metrics.internal:9091"},
                },
            ],
        }
        self.assertEqual(find_prometheus_url(descriptor), "http://metrics.internal:9091")

    def test_prometheus_probe_rejects_missing_metric(self) -> None:
        class _Response:
            status_code = 200

            @staticmethod
            def json():
                return {"status": "success", "data": {"result": []}}

        original_get = prometheus_connector_module.requests.get
        try:
            prometheus_connector_module.requests.get = lambda *args, **kwargs: _Response()
            ok, detail = PrometheusConnector(
                {"url": "http://metrics:9090", "up_query": "missing_metric"},
                {},
            ).health()
        finally:
            prometheus_connector_module.requests.get = original_get

        self.assertFalse(ok)
        self.assertIn("无结果", detail)
        self.assertIn("指标名", detail)

    def test_collect_http_services_skips_prometheus_ports_and_marks_failures(self) -> None:
        original_get = monitoring_service.httpx.get
        original_monotonic = monitoring_service.time.monotonic
        try:
            ticks = iter([10.0, 10.05])

            class _OkResponse:
                status_code = 200

            def fake_get(url, **kwargs):
                if "api.example.com" in url:
                    return _OkResponse()
                raise RuntimeError("boom")

            monitoring_service.httpx.get = fake_get
            monitoring_service.time.monotonic = lambda: next(ticks)

            services = collect_http_services(
                {
                    "services": [
                        {"name": "Prometheus", "connector": "http", "health_url": "http://prom:9090/-/healthy"},
                        {"name": "API", "connector": "http", "health_url": "https://api.example.com/health"},
                        {"name": "Broken", "connector": "http", "health_url": "https://bad.example.com/health"},
                    ]
                }
            )
        finally:
            monitoring_service.httpx.get = original_get
            monitoring_service.time.monotonic = original_monotonic

        self.assertEqual([item.name for item in services], ["API", "Broken"])
        self.assertTrue(services[0].ok)
        self.assertEqual(services[0].latency_ms, 50)
        self.assertFalse(services[1].ok)


class WorkflowServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        db_path = Path(tmpdir.name) / "workflow.db"
        self.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            owner = User(email="owner@example.com", hashed_password="x", org_id=1, role="owner")
            viewer = User(email="viewer@example.com", hashed_password="x", org_id=1, role="viewer")
            system = MonitoredSystem(
                org_id=1,
                key="wf",
                name="工作流系统",
                local=False,
                restart_policy={"authorized_user_ids": []},
            )
            session.add(owner)
            session.add(viewer)
            session.add(system)
            session.commit()
            session.refresh(owner)
            session.refresh(viewer)
            session.refresh(system)
            self.owner = User(id=owner.id, email=owner.email, hashed_password="x", org_id=1, role="owner")
            self.viewer = User(id=viewer.id, email=viewer.email, hashed_password="x", org_id=1, role="viewer")
            self.system = MonitoredSystem(id=system.id, org_id=1, key=system.key, name=system.name, local=system.local, restart_policy=system.restart_policy)

    def test_decide_workflow_rejects_pending_record(self) -> None:
        with Session(self.engine) as session:
            workflow = ActionWorkflow(
                org_id=1,
                system_id=self.system.id,
                thread_id="thread-1",
                question="发生了什么",
                status="pending",
            )
            session.add(workflow)
            session.commit()
            session.refresh(workflow)

            result = self._run_async(
                workflow_service.decide_workflow(
                    session,
                    self.system.id,
                    workflow.id,
                    1,
                    WorkflowDecision(approved=False, reason="先观察"),
                    self.owner,
                )
            )

            session.refresh(workflow)
            audit = session.exec(select(AuditLog).where(AuditLog.event_type == "workflow.rejected")).one()

        self.assertEqual(result.status, "rejected")
        self.assertEqual(workflow.status, "rejected")
        self.assertEqual(workflow.execution_result, "先观察")
        self.assertEqual(audit.actor_id, str(self.owner.id))
        self.assertEqual(audit.target_id, str(workflow.id))

    def test_create_workflow_receives_sanitized_stuck_task_context(self) -> None:
        captured = {}

        async def fake_start_workflow(**kwargs):
            captured.update(kwargs)
            return (
                "thread-stuck",
                "Task-Worker 不可达",
                {"type": "manual", "description": "检查 Worker", "manual_steps": ["查看日志"]},
            )

        original_start = workflow_service.start_workflow
        try:
            workflow_service.start_workflow = fake_start_workflow
            with Session(self.engine) as session:
                result = self._run_async(
                    workflow_service.create_workflow(
                        session,
                        self.system.id,
                        1,
                        WorkflowStart(
                            question="为什么任务一直处理中",
                            context={
                                "analysis_type": "stuck_tasks",
                                "stuck_count": 12,
                                "stuck_threshold_minutes": 30,
                                "worker_health": [
                                    {"name": "Task-Worker", "ok": False, "detail": "connection refused"}
                                ],
                                "sample_rows": [{"owner_email": "secret@example.com"}],
                            },
                        ),
                        self.owner,
                    )
                )
        finally:
            workflow_service.start_workflow = original_start

        self.assertEqual(result.question, "为什么任务一直处理中")
        self.assertIn('"stuck_count": 12', captured["question"])
        self.assertIn("Task-Worker", captured["question"])
        self.assertNotIn("secret@example.com", captured["question"])

    def test_decide_workflow_blocks_repeat_decision(self) -> None:
        with Session(self.engine) as session:
            workflow = ActionWorkflow(
                org_id=1,
                system_id=self.system.id,
                thread_id="thread-2",
                question="发生了什么",
                status="done",
            )
            session.add(workflow)
            session.commit()
            session.refresh(workflow)

            with self.assertRaisesRegex(ValueError, "不可重复决策"):
                self._run_async(
                    workflow_service.decide_workflow(
                        session,
                        self.system.id,
                        workflow.id,
                        1,
                        WorkflowDecision(approved=False),
                        self.owner,
                    )
                )

    def test_decide_workflow_blocks_restart_without_permission(self) -> None:
        with Session(self.engine) as session:
            workflow = ActionWorkflow(
                org_id=1,
                system_id=self.system.id,
                thread_id="thread-3",
                question="重启 Redis",
                status="pending",
                proposed_action={
                    "type": "restart_container",
                    "service": "Redis",
                    "args": {"container": "redis-main"},
                },
            )
            session.add(workflow)
            session.commit()
            session.refresh(workflow)

            with self.assertRaisesRegex(PermissionError, "重启权限"):
                self._run_async(
                    workflow_service.decide_workflow(
                        session,
                        self.system.id,
                        workflow.id,
                        1,
                        WorkflowDecision(approved=True),
                        self.viewer,
                    )
                )

    @staticmethod
    def _run_async(coro):
        import asyncio

        return asyncio.run(coro)


class CollectorAndHealthServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        db_path = Path(tmpdir.name) / "collector.db"
        self.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)

    def test_create_collector_and_record_report_updates_system_snapshot(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(org_id=1, key="edge", name="边缘系统", local=False)
            session.add(system)
            session.commit()
            session.refresh(system)

            created = create_collector(session, system.id, 1, CollectorCreate(name="agent-1"))
            collector = session.get(CollectorExport, created.id)
            result = record_collector_report(
                session,
                collector,
                CollectorReport(services=[HealthItem(name="API", ok=True, detail="ok")]),
            )
            session.refresh(system)
            session.refresh(collector)

        self.assertTrue(created.collector_key)
        self.assertEqual(result["received"], 1)
        self.assertEqual(system.last_health["services"][0]["name"], "API")
        self.assertIsNotNone(system.last_report_at)
        self.assertIsNotNone(collector.last_seen)

    def test_get_system_health_prefers_collector_snapshot(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(
                org_id=1,
                key="edge",
                name="边缘系统",
                local=False,
                last_health={"services": [{"name": "API", "ok": False, "detail": "down", "connector": "http"}]},
            )
            session.add(system)
            session.commit()
            session.refresh(system)
            create_collector(session, system.id, 1, CollectorCreate(name="agent-1"))
            system.last_report_at = system.created_at
            session.add(system)
            session.commit()

            health = get_system_health(session, system.id, 1)

        self.assertEqual(health.source, "collector")
        self.assertFalse(health.healthy)
        self.assertEqual(health.services[0].name, "API")

    def test_collector_restart_command_restarts_registered_container_only(self) -> None:
        original_run = collector_ws_client.subprocess.run
        try:
            collector_ws_client.subprocess.run = lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr="")
            result = collector_ws_client._handle_command(
                "restart_container",
                {"service": "Redis", "container": "redis-main"},
                {
                    "services": [
                        {
                            "name": "Redis",
                            "config": {"container": "redis-main"},
                            "runtime": {"container": "redis-main"},
                        }
                    ]
                },
            )
        finally:
            collector_ws_client.subprocess.run = original_run

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["target"], "redis-main")


class CompatibilitySurfaceTests(unittest.TestCase):
    def test_schema_compatibility_module_reexports(self) -> None:
        self.assertEqual(SystemOut.__name__, "SystemOut")

    def test_descriptor_compatibility_facade_builds_prompt(self) -> None:
        system = MonitoredSystem(org_id=7, key="ops", name="运维平台", local=False, infra={})
        descriptor = system_to_descriptor(system, [])
        prompt = build_prompt(descriptor)
        self.assertIn("运维平台", prompt)
        self.assertIn("接入模式", prompt)

    def test_restart_annotation_and_capability_use_registered_container(self) -> None:
        system = MonitoredSystem(org_id=7, key="ops", name="运维平台", local=True, restart_policy={"authorized_user_ids": [9]})
        services = [{"name": "Redis", "config": {"container": "redis-main"}}]
        action = annotate_restart_action(
            {
                "local": True,
                "services": [
                    {
                        "name": "Redis",
                        "config": {"container": "redis-main"},
                        "runtime": {"container": "redis-main"},
                    }
                ],
            },
            {"type": "restart_container", "service": "Redis", "args": {"container": "redis-main"}},
        )
        capability = build_restart_capability(
            system,
            services,
            collector=None,
            user=User(id=9, email="u@example.com", hashed_password="x", org_id=7, role="viewer"),
        )

        self.assertTrue(action["restart_ready"])
        self.assertEqual(action["execution_mode"], "local")
        self.assertTrue(capability["enabled"])
        self.assertTrue(capability["has_permission"])

    def test_restart_recovery_verification_reports_success_and_failure(self) -> None:
        original_collect_health = workflow_execution.collect_health
        state = {"descriptor": {"services": [{"name": "Redis"}]}}
        action = {"type": "restart_container", "service": "Redis"}
        try:
            workflow_execution.collect_health = lambda descriptor: [
                {"name": "Redis", "ok": True, "detail": "tcp 127.0.0.1:6379 ok"}
            ]
            ok, detail = workflow_execution.verify_action_recovery(state, action)

            workflow_execution.collect_health = lambda descriptor: [
                {"name": "Redis", "ok": False, "detail": "connection refused"}
            ]
            failed_ok, failed_detail = workflow_execution.verify_action_recovery(state, action)
        finally:
            workflow_execution.collect_health = original_collect_health

        self.assertTrue(ok)
        self.assertIn("回查通过", detail)
        self.assertFalse(failed_ok)
        self.assertIn("仍然异常", failed_detail)


class DataAnalysisServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.tmpdir = Path(tmpdir.name)
        db_path = self.tmpdir / "control.db"
        self.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)

        self.business_db = self.tmpdir / "business.db"
        business_engine = create_engine(f"sqlite:///{self.business_db}", connect_args={"check_same_thread": False})
        with business_engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE tasks ("
                "id INTEGER PRIMARY KEY, status TEXT, owner_email TEXT, "
                "created_at TEXT, updated_at TEXT, worker_name TEXT)"
            )
            conn.exec_driver_sql(
                "INSERT INTO tasks (status, owner_email, created_at, updated_at, worker_name) VALUES "
                "('done', 'a@example.com', '2099-01-01 09:00:00', '2099-01-01 09:10:00', 'worker-a'),"
                "('pending', 'b@example.com', '2099-01-01 10:00:00', '2099-01-01 10:00:00', 'worker-a')"
            )
        business_engine.dispose()

    def test_analyze_system_data_counts_and_masks_recent_rows(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(
                org_id=1,
                key="task-app",
                name="任务系统",
                infra={
                    "readonly_database": {
                        "name": "任务库",
                        "url": f"sqlite:///{self.business_db}",
                        "table": "tasks",
                        "timestamp_column": "created_at",
                        "sensitive_fields": ["owner_email"],
                        "max_rows": 5,
                    }
                },
            )
            session.add(system)
            session.commit()
            session.refresh(system)

            result = analyze_system_data(session, system.id, 1, "最近任务记录数量是多少")
            audit = session.exec(select(AuditLog).where(AuditLog.event_type == "data_analysis.queried")).one()

        self.assertIn("任务库", result.answer)
        self.assertIn("2 条", result.answer)
        self.assertEqual(result.evidence["total"], 2)
        self.assertEqual(result.evidence["sample_rows"][0]["owner_email"], "***")
        self.assertEqual(audit.status, "success")

    def test_run_readonly_query_rejects_write_sql(self) -> None:
        with self.assertRaisesRegex(ValueError, "只允许 SELECT"):
            run_readonly_query({"url": f"sqlite:///{self.business_db}"}, "DELETE FROM tasks")

        with self.assertRaisesRegex(ValueError, "写入或结构变更"):
            run_readonly_query({"url": f"sqlite:///{self.business_db}"}, "SELECT * FROM tasks WHERE status = 'drop'")

    def test_readonly_database_config_masks_and_preserves_url(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(org_id=1, key="task-app", name="任务系统")
            session.add(system)
            session.commit()
            session.refresh(system)

            saved = update_readonly_database_config(
                session,
                system.id,
                1,
                ReadonlyDatabaseConfig(
                    enabled=True,
                    name="任务库",
                    database_url=f"sqlite:///{self.business_db}",
                    table="tasks",
                    timestamp_column="created_at",
                ),
            )
            loaded = get_readonly_database_config(session, system.id, 1)
            test_result = test_readonly_database_config(session, system.id, 1)

            preserved = update_readonly_database_config(
                session,
                system.id,
                1,
                ReadonlyDatabaseConfig(
                    enabled=True,
                    name="任务库-新名称",
                    database_url="***",
                    table="tasks",
                    timestamp_column="created_at",
                ),
            )
            session.refresh(system)

        self.assertEqual(saved.database_url, "***")
        self.assertEqual(loaded.database_url, "***")
        self.assertEqual(preserved.database_url, "***")
        self.assertEqual(test_result["total"], 2)
        self.assertEqual(system.infra["readonly_database"]["database_url"], f"sqlite:///{self.business_db}")

    def test_diagnose_data_question_uses_readonly_analysis(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(
                org_id=1,
                key="task-app",
                name="任务系统",
                infra={
                    "readonly_database": {
                        "url": f"sqlite:///{self.business_db}",
                        "table": "tasks",
                        "timestamp_column": "created_at",
                    }
                },
            )
            session.add(system)
            session.commit()
            session.refresh(system)

            response = diagnose_system(session, system.id, 1, "任务数量统计一下")

        self.assertIn("匹配记录数", response.answer)
        self.assertEqual(response.template_name, "readonly_data_analysis")

    def test_stuck_task_analysis_combines_database_worker_health_and_logs(self) -> None:
        business_engine = create_engine(f"sqlite:///{self.business_db}")
        with business_engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO tasks "
                "(status, owner_email, created_at, updated_at, worker_name) VALUES "
                "('processing', 'stuck@example.com', '2000-01-01 00:00:00', "
                "'2000-01-01 00:05:00', 'worker-a')"
            )
        business_engine.dispose()

        original_health = data_analysis_service.collect_health
        original_logs = data_analysis_service.read_service_logs
        try:
            data_analysis_service.collect_health = lambda descriptor: [
                {
                    "name": "Task-Worker",
                    "ok": False,
                    "detail": "connection refused",
                    "connector": "tcp",
                }
            ]
            data_analysis_service.read_service_logs = lambda *args, **kwargs: "ERROR downstream timeout"
            with Session(self.engine) as session:
                system = MonitoredSystem(
                    org_id=1,
                    key="task-app",
                    name="任务系统",
                    infra={
                        "readonly_database": {
                            "enabled": True,
                            "name": "任务库",
                            "url": f"sqlite:///{self.business_db}",
                            "table": "tasks",
                            "timestamp_column": "created_at",
                            "task_analysis_enabled": True,
                            "task_id_column": "id",
                            "status_column": "status",
                            "updated_at_column": "updated_at",
                            "worker_column": "worker_name",
                            "processing_values": ["processing"],
                            "stuck_threshold_minutes": 30,
                            "worker_service_names": ["Task-Worker"],
                        }
                    },
                )
                session.add(system)
                session.commit()
                session.refresh(system)
                session.add(Service(
                    system_id=system.id,
                    name="Task-Worker",
                    connector="tcp",
                    config={"host": "127.0.0.1", "port": 9000},
                    enabled=True,
                ))
                session.commit()

                result = analyze_system_data(session, system.id, 1, "为什么任务一直处理中")
                audit = session.exec(
                    select(AuditLog).where(AuditLog.event_type == "task_stuck.analyzed")
                ).one()
        finally:
            data_analysis_service.collect_health = original_health
            data_analysis_service.read_service_logs = original_logs

        self.assertEqual(result.evidence["analysis_type"], "stuck_tasks")
        self.assertEqual(result.evidence["stuck_count"], 1)
        self.assertEqual(result.evidence["sample_rows"][0]["worker_name"], "worker-a")
        self.assertEqual(result.evidence["worker_health"][0]["ok"], False)
        self.assertIn("downstream timeout", result.evidence["worker_logs"][0]["excerpt"])
        self.assertIn("先恢复异常 Worker", result.answer)
        self.assertEqual(audit.output["stuck_count"], 1)

    def test_readonly_config_validates_task_analysis_columns(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(org_id=1, key="task-app", name="任务系统")
            session.add(system)
            session.commit()
            session.refresh(system)
            update_readonly_database_config(
                session,
                system.id,
                1,
                ReadonlyDatabaseConfig(
                    enabled=True,
                    database_url=f"sqlite:///{self.business_db}",
                    table="tasks",
                    task_analysis_enabled=True,
                    task_id_column="id",
                    status_column="status",
                    updated_at_column="updated_at",
                    processing_values=["processing"],
                ),
            )
            test_result = test_readonly_database_config(session, system.id, 1)

        self.assertTrue(test_result["task_analysis_ready"])

    def test_diagnose_routes_stuck_task_question_to_specialized_analysis(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(
                org_id=1,
                key="task-app",
                name="任务系统",
                infra={
                    "readonly_database": {
                        "enabled": True,
                        "url": f"sqlite:///{self.business_db}",
                        "table": "tasks",
                        "task_analysis_enabled": True,
                        "status_column": "status",
                        "updated_at_column": "updated_at",
                        "processing_values": ["processing"],
                    }
                },
            )
            session.add(system)
            session.commit()
            session.refresh(system)

            response = diagnose_system(session, system.id, 1, "为什么卡住了")

        self.assertEqual(response.template_name, "stuck_task_analysis")
        self.assertEqual(response.template_description, "任务卡住专项分析")
        self.assertIn("卡住任务", response.answer)


class DiagnoseAndWebhookServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        db_path = Path(tmpdir.name) / "diag.db"
        self.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)

    def test_diagnose_system_wraps_agent_answer(self) -> None:
        original_diagnose = diagnose_service.diagnose_with_details
        try:
            diagnose_service.diagnose_with_details = lambda descriptor, question, **kwargs: DiagnosisRun(
                answer=f"ANSWER:{question}:{descriptor['name']}",
                model="test-model",
                duration_ms=12,
                total_tokens=18,
                tool_calls=[{"tool": "query_prometheus", "status": "success"}],
            )
            with Session(self.engine) as session:
                system = MonitoredSystem(org_id=1, key="ops", name="运维平台", local=False)
                session.add(system)
                session.commit()
                session.refresh(system)
                response = diagnose_system(session, system.id, 1, "为什么慢")
                audit = session.exec(
                    select(AuditLog).where(AuditLog.event_type == "diagnosis.completed")
                ).one()
        finally:
            diagnose_service.diagnose_with_details = original_diagnose

        self.assertIsInstance(response, DiagnoseResponse)
        self.assertEqual(response.system_id, system.id)
        self.assertIn("为什么慢", response.answer)
        self.assertEqual(response.template_name, "performance_analysis")
        self.assertEqual(response.duration_ms, 12)
        self.assertEqual(audit.input["template_name"], "performance_analysis")
        self.assertEqual(audit.output["tool_calls"][0]["tool"], "query_prometheus")

    def test_diagnostic_templates_can_be_disabled_per_system(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(org_id=1, key="ops", name="运维平台", local=False)
            session.add(system)
            session.commit()
            session.refresh(system)

            templates = list_diagnostic_templates(session, system.id, 1)
            names = {item.name for item in templates}
            self.assertTrue({"service_unreachable", "redis_analysis", "kafka_analysis", "database_analysis", "http_health_failure"} <= names)

            updated = update_diagnostic_templates(
                session,
                system.id,
                1,
                DiagnosticTemplateSettingsUpdate(disabled_names=["redis_analysis"]),
                actor_id="7",
            )
            redis_template = next(item for item in updated if item.name == "redis_analysis")
            audit = session.exec(
                select(AuditLog).where(AuditLog.event_type == "diagnostic_templates.updated")
            ).one()

        self.assertFalse(redis_template.enabled)
        self.assertEqual(audit.actor_id, "7")

    def test_execute_collector_command_rejects_invalid_command(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(org_id=1, key="ops", name="运维平台", local=False)
            session.add(system)
            session.commit()
            session.refresh(system)
            with self.assertRaisesRegex(ValueError, "不支持的命令"):
                self._run_async(
                    execute_collector_command(
                        session,
                        system.id,
                        1,
                        CollectorExecRequest(cmd="rm_all", args={}),
                    )
                )

    def test_process_webhook_payload_handles_challenge_and_dedup(self) -> None:
        class _Background:
            def __init__(self):
                self.calls = []

            def add_task(self, *args):
                self.calls.append(args)

        bg = _Background()
        result = self._run_async(feishu_webhook_service.process_webhook_payload({"challenge": "abc"}, bg))
        self.assertEqual(result, {"challenge": "abc"})

        payload = {"header": {"event_type": "noop", "event_id": "evt-1"}}
        first = self._run_async(feishu_webhook_service.process_webhook_payload(payload, bg))
        second = self._run_async(feishu_webhook_service.process_webhook_payload(payload, bg))
        self.assertEqual(first, {"code": 0})
        self.assertEqual(second, {"code": 0})
        self.assertEqual(bg.calls, [])

    def test_diagnose_facade_exports_runner_and_model_picker(self) -> None:
        diagnose_facade = importlib.import_module("app.agent.diagnostics.runner")
        self.assertTrue(callable(diagnose_facade.diagnose))
        self.assertTrue(callable(diagnose_facade.pick_model))

    def test_workflow_facade_exports_runner_and_graph(self) -> None:
        workflow_facade = importlib.import_module("app.agent.workflows.runner")
        self.assertTrue(callable(workflow_facade.start_workflow))
        self.assertTrue(callable(workflow_facade.resume_workflow))
        self.assertIsNotNone(workflow_facade.approval_graph)

    def test_pick_model_uses_advanced_model_for_high_risk_question(self) -> None:
        model = diagnose_models.pick_model("P0 故障，帮我根因分析")
        self.assertEqual(model.model_name, diagnose_models.advanced_model().model_name)

    def test_pick_model_uses_default_model_for_normal_question(self) -> None:
        model = diagnose_models.pick_model("Redis 当前内存多少")
        self.assertEqual(model.model_name, diagnose_models.default_model().model_name)

    @staticmethod
    def _run_async(coro):
        import asyncio

        return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()

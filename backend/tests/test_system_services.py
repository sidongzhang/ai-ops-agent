import tempfile
import unittest
import importlib
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from app.models.systems import MonitoredSystem
from app.models.workflows import ActionWorkflow
from app.models import Collector as CollectorExport
from app.schemas import NotifyConfig, SystemCreate, WorkflowDecision
from app.schemas.diagnostics import DiagnoseResponse
from app.schemas.collectors import CollectorCreate, CollectorReport
from app.schemas.health import HealthItem
from app.schemas import CollectorExecRequest
from app.services.collectors.exec import execute_collector_command
from app.services.collectors.service import create_collector, record_collector_report
from app.services.diagnostics.service import diagnose_system
from app.services.monitoring.health import get_system_health
from app.schemas.systems import SystemOut
from app.services.monitoring import metrics as monitoring_service
from app.services.notifications import webhook as feishu_webhook_service
from app.services.workflows import service as workflow_service
from app.services.diagnostics import service as diagnose_service
from app.services.descriptors.builder import system_to_descriptor
from app.services.descriptors.prompt import build_prompt
from app.agent.diagnostics import models as diagnose_models
from app.services.monitoring.metrics import collect_http_services, find_prometheus_url
from app.services.notifications.config import merge_notify_config, send_test_notification
from app.services.systems.service import create_system, update_notify


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


class NotifyServiceTests(unittest.TestCase):
    def test_merge_notify_config_preserves_existing_secret(self) -> None:
        merged = merge_notify_config(
            NotifyConfig(type="feishu", app_id="cli_123", app_secret="***", chat_id="oc_new"),
            {"type": "feishu", "app_id": "cli_123", "app_secret": "enc:stored", "chat_id": "oc_old"},
        )
        self.assertEqual(merged["app_secret"], "enc:stored")
        self.assertEqual(merged["chat_id"], "oc_new")

    def test_send_test_notification_dispatches_feishu(self) -> None:
        _FakeFeishuClient.sent.clear()
        send_test_notification(
            "订单系统",
            {"type": "feishu", "app_id": "cli_123", "app_secret": "sec", "chat_id": "oc_1"},
            feishu_client_cls=_FakeFeishuClient,
        )
        self.assertEqual(_FakeFeishuClient.sent, [("cli_123", "sec", "oc_1", "订单系统")])

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


class MonitoringServiceTests(unittest.TestCase):
    def test_find_prometheus_url_prefers_registered_service(self) -> None:
        descriptor = {
            "infra": {"prometheus_url": "http://fallback:9090"},
            "services": [
                {"name": "Prometheus", "health_url": "http://prometheus.internal:9090/-/healthy"},
            ],
        }
        self.assertEqual(find_prometheus_url(descriptor), "http://prometheus.internal:9090")

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

    def test_decide_workflow_rejects_pending_record(self) -> None:
        with Session(self.engine) as session:
            workflow = ActionWorkflow(
                org_id=1,
                system_id=9,
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
                    9,
                    workflow.id,
                    1,
                    WorkflowDecision(approved=False, reason="先观察"),
                )
            )

            session.refresh(workflow)

        self.assertEqual(result.status, "rejected")
        self.assertEqual(workflow.status, "rejected")
        self.assertEqual(workflow.execution_result, "先观察")

    def test_decide_workflow_blocks_repeat_decision(self) -> None:
        with Session(self.engine) as session:
            workflow = ActionWorkflow(
                org_id=1,
                system_id=9,
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
                        9,
                        workflow.id,
                        1,
                        WorkflowDecision(approved=False),
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


class CompatibilitySurfaceTests(unittest.TestCase):
    def test_schema_compatibility_module_reexports(self) -> None:
        self.assertEqual(SystemOut.__name__, "SystemOut")

    def test_descriptor_compatibility_facade_builds_prompt(self) -> None:
        system = MonitoredSystem(org_id=7, key="ops", name="运维平台", local=False, infra={})
        descriptor = system_to_descriptor(system, [])
        prompt = build_prompt(descriptor)
        self.assertIn("运维平台", prompt)
        self.assertIn("接入模式", prompt)


class DiagnoseAndWebhookServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        db_path = Path(tmpdir.name) / "diag.db"
        self.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)

    def test_diagnose_system_wraps_agent_answer(self) -> None:
        original_diagnose = diagnose_service.diagnose
        try:
            diagnose_service.diagnose = lambda descriptor, question, **kwargs: f"ANSWER:{question}:{descriptor['name']}"
            with Session(self.engine) as session:
                system = MonitoredSystem(org_id=1, key="ops", name="运维平台", local=False)
                session.add(system)
                session.commit()
                session.refresh(system)
                response = diagnose_system(session, system.id, 1, "为什么慢")
        finally:
            diagnose_service.diagnose = original_diagnose

        self.assertIsInstance(response, DiagnoseResponse)
        self.assertEqual(response.system_id, system.id)
        self.assertIn("为什么慢", response.answer)

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

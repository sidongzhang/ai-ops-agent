import tempfile
import unittest
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

from app.models.diagnostics import DiagnosisReport
from app.models.incidents import Incident
from app.models.messages import SystemMessage
from app.models.systems import MonitoredSystem
from app.models.workflows import ActionWorkflow
from app.services.diagnostics.reports import export_report_to_knowledge
from app.services.incidents.service import (
    backfill_incidents,
    build_incident_workflow_question,
    incident_summary,
    list_incident_outputs,
)
from app.services.messages import create_alert_message
from app.services.monitoring.metrics import prom_query_range
from app.services.workflows.actions import normalize_action
from app.services.workflows.service import list_org_workflows, list_pending_workflows


class RoadmapFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        db_path = Path(tmpdir.name) / "roadmap.db"
        self.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)

    def test_alert_message_creates_incident_with_system_name(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(org_id=1, key="demo", name="演示系统", local=True)
            session.add(system)
            session.commit()
            session.refresh(system)

            create_alert_message(session, system, ["Redis"])
            session.commit()

            incidents = list_incident_outputs(session, 1)
            self.assertEqual(len(incidents), 1)
            self.assertEqual(incidents[0].system_name, "演示系统")
            self.assertEqual(incidents[0].status, "open")
            self.assertIn("Redis", incidents[0].failed_services)

            summary = incident_summary(session, 1)
            self.assertEqual(summary.total, 1)
            self.assertEqual(summary.open, 1)

    def test_related_alerts_merge_into_same_incident(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(org_id=1, key="demo", name="演示系统", local=True)
            session.add(system)
            session.commit()
            session.refresh(system)

            create_alert_message(session, system, ["Redis"])
            create_alert_message(session, system, ["Redis", "Kafka"])
            session.commit()

            incidents = list_incident_outputs(session, 1)
            self.assertEqual(len(incidents), 1)
            self.assertEqual(incidents[0].message_count, 2)
            self.assertEqual(set(incidents[0].failed_services), {"Redis", "Kafka"})

    def test_export_diagnosis_report_to_knowledge(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(org_id=1, key="demo", name="演示系统", local=True)
            session.add(system)
            session.commit()
            session.refresh(system)

            report = DiagnosisReport(
                org_id=1,
                system_id=system.id,
                report_type="diagnose",
                status="success",
                question="Kafka 为什么 lag 很高？",
                answer="消费者处理变慢，建议扩容 Worker。",
                evidence_steps=["检查 Kafka lag", "检查 Worker 健康"],
            )
            session.add(report)
            session.commit()
            session.refresh(report)

            doc = export_report_to_knowledge(
                session,
                system.id,
                1,
                report.id,
                doc_name="kafka-lag-analysis",
            )
            self.assertTrue(doc.name.endswith(".md"))
            self.assertGreater(doc.size, 0)

    def test_org_workflow_list_includes_system_name(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(org_id=1, key="demo", name="演示系统", local=True)
            session.add(system)
            session.commit()
            session.refresh(system)

            pending = ActionWorkflow(
                org_id=1,
                system_id=system.id,
                thread_id="t-1",
                question="Redis 内存异常怎么修？",
                diagnosis="建议重启 Redis 并检查大 key。",
                proposed_action={"type": "restart_container", "service": "Redis"},
                status="pending",
            )
            done = ActionWorkflow(
                org_id=1,
                system_id=system.id,
                thread_id="t-2",
                question="Kafka lag 怎么处理？",
                diagnosis="已执行扩容建议。",
                proposed_action={"type": "manual"},
                status="done",
                execution_result="人工处理完成",
            )
            session.add(pending)
            session.add(done)
            session.commit()

            pending_rows = list_pending_workflows(session, 1)
            processed_rows = list_org_workflows(session, 1, status="processed")
            self.assertEqual(len(pending_rows.items), 1)
            self.assertEqual(pending_rows.items[0].system_name, "演示系统")
            self.assertEqual(len(processed_rows.items), 1)
            self.assertEqual(processed_rows.items[0].status, "done")

            done_rows = list_org_workflows(session, 1, status="done")
            self.assertEqual(len(done_rows.items), 1)
            self.assertEqual(done_rows.items[0].proposed_action["display_name"], "人工处理")

    def test_remote_redis_action_is_downgraded_to_manual(self) -> None:
        action = normalize_action(
            {
                "type": "run_redis_command",
                "service": "Redis",
                "args": {"command": "FLUSHDB"},
            },
            descriptor={"local": False},
        )

        self.assertEqual(action["type"], "manual")
        self.assertEqual(action["risk"], "manual")
        self.assertIn("远程系统", action["description"])

    def test_incident_workflow_question_carries_context(self) -> None:
        incident = Incident(
            id=9,
            org_id=1,
            system_id=2,
            title="系统「演示系统」服务异常",
            failed_services=["Redis", "Kafka"],
            message_count=2,
            severity="critical",
        )
        messages = [
            SystemMessage(
                org_id=1,
                system_id=2,
                title="Redis 无法访问",
                summary="Redis tcp 检查失败",
                severity="critical",
                status="unread",
            )
        ]

        question, context = build_incident_workflow_question(incident, messages)

        self.assertIn("Redis、Kafka", question)
        self.assertEqual(context["source"], "incident")
        self.assertEqual(context["incident_id"], 9)
        self.assertEqual(context["failed_services"], ["Redis", "Kafka"])
        self.assertEqual(context["messages"][0]["title"], "Redis 无法访问")

    def test_backfill_incidents_links_legacy_alert_messages(self) -> None:
        with Session(self.engine) as session:
            system = MonitoredSystem(org_id=1, key="demo", name="演示系统", local=True)
            session.add(system)
            session.commit()
            session.refresh(system)

            legacy = SystemMessage(
                org_id=1,
                system_id=system.id,
                message_type="alert",
                severity="warning",
                title="系统「演示系统」存在异常服务",
                summary="异常服务：Redis、Kafka",
                content="平台巡检发现系统「演示系统」以下服务从正常变为异常：Redis、Kafka。",
                source="scheduled_health_check",
                related={"failed_services": ["Redis", "Kafka"]},
            )
            session.add(legacy)
            session.commit()

            linked = backfill_incidents(session)
            self.assertEqual(linked, 1)

            incidents = list_incident_outputs(session, 1)
            self.assertEqual(len(incidents), 1)
            self.assertEqual(incidents[0].message_count, 1)
            self.assertEqual(set(incidents[0].failed_services), {"Redis", "Kafka"})
            session.refresh(legacy)
            self.assertEqual(legacy.incident_id, incidents[0].id)

    def test_prom_query_range_uses_remote_range_reader(self) -> None:
        calls = []

        def remote_range(promql: str, start: float, end: float, step: str):
            calls.append((promql, start, end, step))
            return [{"values": [[start, "12.5"], [end, "15.0"]]}]

        results = prom_query_range(
            "",
            'avg(rate(node_cpu_seconds_total{mode!="idle"}[2m])) * 100',
            100.0,
            200.0,
            "5m",
            query_fn=lambda _promql: [],
            range_query_fn=remote_range,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0]["values"]), 2)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()

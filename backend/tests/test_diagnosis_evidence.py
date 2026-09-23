import unittest

from app.services.diagnostics.evidence import (
    build_evidence_from_data_analysis,
    build_evidence_from_tool_calls,
    build_evidence_steps,
    build_knowledge_refs,
)


class DiagnosisEvidenceTests(unittest.TestCase):
    def test_build_evidence_from_tool_calls(self) -> None:
        items = build_evidence_from_tool_calls([
            {
                "tool": "query_prometheus",
                "status": "success",
                "input": {"query": "up"},
                "output": "metric ok",
                "duration_ms": 120,
            }
        ])
        self.assertEqual(items[0]["label"], "Prometheus 指标")
        self.assertEqual(items[0]["detail"], "metric ok")
        steps = build_evidence_steps([
            {"tool": "query_prometheus", "status": "success", "output": "metric ok"},
        ])
        self.assertIn("Prometheus 指标", steps[0])

    def test_build_knowledge_refs(self) -> None:
        from unittest.mock import patch

        with patch("app.services.diagnostics.evidence.search_with_memories") as search_mock, patch(
            "app.services.diagnostics.evidence.read_doc"
        ) as read_mock:
            search_mock.return_value = [{"name": "runbook.md", "snippet": "Redis 内存高", "score": 0.9}]
            read_mock.return_value = "# Runbook\n\n完整排查步骤"
            refs = build_knowledge_refs(
                1,
                [{"tool": "search_knowledge_base", "input": {"query": "Redis 内存"}}],
            )
        self.assertEqual(refs[0]["name"], "runbook.md")
        self.assertIn("完整排查步骤", refs[0]["content"])

    def test_build_knowledge_refs_from_question_when_no_tool_call(self) -> None:
        from unittest.mock import patch

        with patch("app.services.diagnostics.evidence.search_with_memories") as search_mock, patch(
            "app.services.diagnostics.evidence.read_doc"
        ) as read_mock:
            search_mock.return_value = [
                {"name": "test-collector-offline.md", "snippet": "蓝鲸 COLLECTOR-OFFLINE-77", "score": 4.0}
            ]
            read_mock.return_value = "# 采集器离线\n\ndocker ps | grep aiops-collector"
            refs = build_knowledge_refs(2, [], question="蓝鲸 COLLECTOR-OFFLINE-77")
        self.assertEqual(refs[0]["name"], "test-collector-offline.md")
        search_mock.assert_called_with("蓝鲸 COLLECTOR-OFFLINE-77", "2")

    def test_build_evidence_from_data_analysis(self) -> None:
        items, steps = build_evidence_from_data_analysis({
            "data_source": "orders",
            "table": "orders",
            "today_only": True,
            "total": 12,
            "count_sql": "SELECT COUNT(*) AS total FROM orders",
        })
        self.assertGreaterEqual(len(items), 2)
        self.assertGreaterEqual(len(steps), 2)


if __name__ == "__main__":
    unittest.main()

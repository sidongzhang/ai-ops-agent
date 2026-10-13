"""护栏：`success` 但完全没有取证的报告必须能被识别出来。

背景（实测）：模型会给出"没干活却像干完了"的结论，状态仍是 `success`。例如本地 3B 对
「TCAT 切割任务有几条没完成」**未调用任何工具**就回答"我没有直接获取数据的能力"；
业务数据集类问题 3/3 失败。若不标记，使用者会误以为结论有依据。

这里锁住派生规则的边界：只有"成功 + 有结论 + 零取证"才标记；有知识库引用、有工具调用、
失败/取消、空结论都不标记（避免误报）。
"""
import unittest

from app.models.diagnostics import DiagnosisReport


def _report(**kwargs) -> DiagnosisReport:
    base = dict(org_id=1, system_id=1, question="q", status="success", answer="结论：一切正常")
    base.update(kwargs)
    return DiagnosisReport(**base)


class UnverifiedReasonTests(unittest.TestCase):
    def test_flagged_when_success_without_any_evidence(self) -> None:
        report = _report(answer="我没有获取数据的能力，无法回答。")
        self.assertEqual(report.unverified_reason, "no_tool_evidence")

    def test_not_flagged_when_tools_were_called(self) -> None:
        report = _report(tool_calls=[{"tool": "run_redis_command"}])
        self.assertEqual(report.unverified_reason, "")

    def test_not_flagged_when_evidence_steps_present(self) -> None:
        report = _report(evidence=[{"label": "Redis 只读命令", "status": "ok"}])
        self.assertEqual(report.unverified_reason, "")

    def test_not_flagged_when_evidence_sources_present(self) -> None:
        report = _report(evidence_sources=["logs"])
        self.assertEqual(report.unverified_reason, "")

    def test_not_flagged_when_answer_cites_retrieved_doc(self) -> None:
        """纯知识库回答只要**写出文档名**就算有依据（真实案例 #161）。"""
        report = _report(
            answer="**结论**：切割窗口为 [T0-50s, T0+250s]（依据 多星联合分析流程_v3.md）。",
            knowledge_refs=[{"name": "多星联合分析流程_v3.md", "score": 0.75}],
        )
        self.assertEqual(report.unverified_reason, "")

    def test_flagged_when_knowledge_retrieved_but_not_cited(self) -> None:
        """检索到文档但答案根本没引用（也就是"没依据"）→ 必须标记（真实案例 #268）。"""
        report = _report(
            answer="当前没有足够的信息能够给出精确的回答，我没有直接获取数据的能力。",
            knowledge_refs=[{"name": "多星联合分析流程.md", "score": 0.82}],
        )
        self.assertEqual(report.unverified_reason, "no_tool_evidence")

    def test_citation_without_extension_also_counts(self) -> None:
        report = _report(
            answer="依据 多星联合分析流程_v3 的规定，窗口为 300s。",
            knowledge_refs=[{"name": "多星联合分析流程_v3.md"}],
        )
        self.assertEqual(report.unverified_reason, "")

    def test_not_flagged_for_non_success_or_empty_answer(self) -> None:
        self.assertEqual(_report(status="failed", error_message="boom").unverified_reason, "")
        self.assertEqual(_report(status="cancelled").unverified_reason, "")
        self.assertEqual(_report(status="running").unverified_reason, "")
        self.assertEqual(_report(answer="   ").unverified_reason, "")

    def test_real_case_from_local_3b_run(self) -> None:
        """复现实测那条：本地 3B 未调工具就回答"没有获取数据的能力"，必须被标记。"""
        report = _report(
            model="aiops-qwen2.5-3b",
            answer="针对用户问题，关于TCAT切割任务中哪些任务没有完成的情况，当前没有足够的信息能够给出精确的回答。",
            tool_calls=[],
            evidence=[],
            evidence_sources=[],
            knowledge_refs=[],
        )
        self.assertEqual(report.unverified_reason, "no_tool_evidence")


if __name__ == "__main__":
    unittest.main()

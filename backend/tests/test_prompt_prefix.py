"""系统提示的「稳定前缀 + 易变后缀」性质（前缀缓存友好）。

守卫点：provider 的 prefix cache 只对**从第一个字符起完全一致**的前缀生效。
一旦有人把"按问题/按时刻变化"的内容放回前缀（曾经是技能步骤、日期），
每次请求都会在很前面分叉，后面几千 token 全价重算——**不会报错**，
只表现为"越用越慢、越用越贵"。

重构后拼装入口迁到 `app.agent.harness.context.build_system_prompt`；
稳定段 = 运行规则 + 数据集/规程/拓扑/MCP 目录，易变段 = 技能/时间/变更/故障/对话/截图/知识库。
"""
import unittest

from app.agent.diagnostics.tools import AgentDeps
from app.agent.harness.context import build_system_prompt

_BASE = {"descriptor": {"name": "demo", "services": []}}


def _common_prefix_len(a: str, b: str) -> int:
    index = 0
    while index < min(len(a), len(b)) and a[index] == b[index]:
        index += 1
    return index


def _prompt(*, question: str, skill: str, knowledge: str, clock: str) -> str:
    return build_system_prompt(
        AgentDeps(
            **_BASE,
            question=question,
            data_catalog="run_daily：联合分析运行量",
            runbook_catalog="## 规程目录\nkafka-lag",
            topology_catalog="## 服务依赖\nSpring Boot → MySQL",
            knowledge_context=knowledge,
            skill_steps=skill,
            clock_hint=clock,
        )
    )


class PromptPrefixOrderTests(unittest.TestCase):
    def test_volatile_content_sits_after_stable_prefix(self) -> None:
        a = _prompt(question="Q_A", skill="SKILL_MARKER_A", knowledge="KNOWLEDGE_MARKER_A", clock="CLOCK_MARKER_A")
        b = _prompt(question="Q_B", skill="SKILL_MARKER_B", knowledge="KNOWLEDGE_MARKER_B", clock="CLOCK_MARKER_B")
        prefix = _common_prefix_len(a, b)

        self.assertGreater(
            prefix / len(a), 0.5,
            "两个不同问题的系统提示公共前缀过短——易变内容被放到了靠前位置，前缀缓存会失效",
        )
        head = a[:prefix]
        for stable in ("上下文与权限边界", "可用业务数据集", "规程目录", "服务拓扑"):
            self.assertIn(stable, head, f"稳定内容「{stable}」必须位于可缓存前缀内")
        for volatile in ("CLOCK_MARKER", "KNOWLEDGE_MARKER"):
            self.assertNotIn(volatile, head, f"易变内容「{volatile}」不应出现在可缓存前缀里")

    def test_clock_and_knowledge_are_at_the_tail(self) -> None:
        prompt = _prompt(question="问题", skill="SKILL", knowledge="KNOWLEDGE_MARKER_X", clock="CLOCK_MARKER_X")
        self.assertLess(prompt.index("可用业务数据集"), prompt.index("CLOCK_MARKER_X"))
        self.assertLess(prompt.index("CLOCK_MARKER_X"), prompt.index("KNOWLEDGE_MARKER_X"))

    def test_skill_steps_are_not_in_stable_prefix(self) -> None:
        a = _prompt(question="Q_A", skill="SKILL_MARKER_A", knowledge="K", clock="CLOCK_MARKER")
        b = _prompt(question="Q_B", skill="SKILL_MARKER_B", knowledge="K", clock="CLOCK_MARKER")
        head = a[: _common_prefix_len(a, b)]
        # 公共前缀会切进技能标记内部（"SKILL_MARKER_"），但完整标记与时间块都不应在前缀里
        self.assertNotIn("SKILL_MARKER_A", head)
        self.assertNotIn("CLOCK_MARKER", head)


class DataCatalogClockTests(unittest.TestCase):
    def test_dataset_catalog_has_no_date_line(self) -> None:
        """数据集目录输出里不应再出现「当前日期」——它每天变化，会把缓存前缀打散。"""
        from app.services.data_analysis import describe_readonly_datasets

        catalog = describe_readonly_datasets(
            {"datasets": [{"code": "run_daily", "view": "run_daily", "label": "联合分析运行量"}]}
        )
        self.assertIn("run_daily", catalog)
        self.assertNotIn("当前日期", catalog, "日期必须移到易变后缀（clock_hint）")


if __name__ == "__main__":
    unittest.main()

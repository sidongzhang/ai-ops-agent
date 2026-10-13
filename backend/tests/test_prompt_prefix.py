"""系统提示的"稳定前缀 + 易变后缀"性质（前缀缓存友好）。

为什么值得锁：provider 的 prefix cache 只对**从第一个字符起完全一致**的前缀生效。
一旦有人把"按问题/按时刻变化"的内容放回前缀里（曾经的 skill_steps、日期），
每次请求都会在很前面分叉，后面几千 token 全部按全价重新 prefill —— 直接表现为
"越用越慢、越用越贵"，而且**不会报错**，很难发现。
"""
import unittest

from app.agent.diagnostics.tools import AgentDeps, build_system_prompt

_BASE = {"descriptor": {"name": "demo", "services": []}}


def _common_prefix_len(a: str, b: str) -> int:
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    return i


class PromptPrefixOrderTests(unittest.TestCase):
    def _prompt(self, *, question: str, skill: str, knowledge: str, clock: str) -> str:
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
                image_count=0,
            )
        )

    def test_volatile_content_sits_after_stable_prefix(self) -> None:
        # 用**唯一标记**定位各段，避免与 base 提示里的通用词撞车
        a = self._prompt(question="Q1", skill="SKILL_MARKER_A",
                         knowledge="KNOWLEDGE_MARKER_A", clock="CLOCK_MARKER_A")
        b = self._prompt(question="Q2", skill="SKILL_MARKER_B",
                         knowledge="KNOWLEDGE_MARKER_B", clock="CLOCK_MARKER_B")
        prefix = _common_prefix_len(a, b)
        self.assertGreater(
            prefix / len(a), 0.5,
            "两个不同问题的系统提示公共前缀过短——说明易变内容被放到了靠前位置，前缀缓存会失效",
        )
        head = a[:prefix]
        # 稳定段（直到"安全边界"）都必须落在可缓存前缀里
        self.assertIn("## 安全边界", head, "稳定段「安全边界」必须位于可缓存前缀内")
        # 靠后的易变段（时间/知识库检索）不能落在前缀里
        for volatile in ("CLOCK_MARKER", "KNOWLEDGE_MARKER"):
            self.assertNotIn(volatile, head, f"易变内容「{volatile}」不应出现在可缓存前缀里")

    def test_stable_parts_are_in_prefix(self) -> None:
        a = self._prompt(question="Q1", skill="SKILL_MARKER_A", knowledge="KNOWLEDGE_MARKER_A", clock="CLOCK_MARKER_A")
        head = a[: _common_prefix_len(a, self._prompt(
            question="Q2", skill="SKILL_MARKER_B", knowledge="KNOWLEDGE_MARKER_B", clock="CLOCK_MARKER_B"))]
        for stable in ("可用业务数据集", "规程目录", "服务依赖", "安全边界"):
            self.assertIn(stable, head, f"稳定内容「{stable}」应在前缀里")

    def test_system_prompt_does_not_embed_a_date_inside_data_catalog(self) -> None:
        """日期属于易变内容：只能出现在 clock_hint（末尾），不能混进数据集目录。"""
        prompt = self._prompt(question="Q", skill="S", knowledge="K", clock="CLOCK_MARKER_X")
        catalog_at = prompt.index("可用业务数据集")
        clock_at = prompt.index("CLOCK_MARKER_X")
        self.assertLess(catalog_at, clock_at, "时间提示必须在数据集目录之后（靠尾部）")


class DataCatalogClockTests(unittest.TestCase):
    def test_dataset_catalog_has_no_date_line(self) -> None:
        """数据集目录**输出**里不应再出现「当前日期」——它每天变化，会把缓存前缀打散。"""
        from app.services.data_analysis import describe_readonly_datasets

        catalog = describe_readonly_datasets(
            {"datasets": [{"code": "run_daily", "view": "run_daily", "label": "联合分析运行量"}]}
        )
        self.assertIn("run_daily", catalog)
        self.assertNotIn("当前日期", catalog, "日期必须移到易变后缀（clock_hint）")


if __name__ == "__main__":
    unittest.main()

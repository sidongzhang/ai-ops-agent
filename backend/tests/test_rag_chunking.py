"""知识库分块与"子代理带知识库"的回归测试。

背景（实测缺陷）：`_chunk()` 原来按**空行**切 markdown，而标题行自身自成一段 →
索引里塞进大量 5~15 字碎片。system 3 的 351 个分块中 **172 个不足 20 字**，
短文本向量相似度又容易偏高，于是 top-k 全是 "约束与规则：" 这类标题碎片，
把真正的正文挤出结果 → 表现为「检索到了却答不出」（报告 #273 注入仅 180 字）。

另一处缺陷：子代理（investigate / 专家子代理）的 deps 里**没有 knowledge_context**，
于是"委派取证"这条路上干活的子代理手里没有文档。
"""
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.agent.diagnostics.knowledge import store
from app.agent.diagnostics.tools import AgentDeps, _sub_deps

DOC = """# 多星联合分析流程

## 约束与规则：

- 外部触发 T0 与 TCat T0 的时间差需在配置窗口内
- 双方均有有效坐标时角距离不超过误差半径
- TCat 状态有效且未被撤销

## 3. 总体业务流程

联合切割组件 CUT 负责把触发天区对应的观测图像按需裁切出各卫星局部图，
为后续搜索与分析准备输入；切割未完成会阻塞下游搜索与联合分析。

## 建议索引：

idx_a
"""


class ChunkingTests(unittest.TestCase):
    def test_bare_headings_are_not_standalone_chunks(self) -> None:
        chunks = store._chunk(DOC)
        for chunk in chunks:
            stripped = chunk.strip()
            self.assertFalse(
                store._HEADING_RE.match(stripped) and "\n" not in stripped,
                f"标题不应单独成块：{stripped!r}",
            )

    def test_no_tiny_fragments(self) -> None:
        """除最后一块外，不应有 < CHUNK_MIN_CHARS 的碎片（标题碎片是主要污染源）。"""
        chunks = store._chunk(DOC)
        small = [c for c in chunks if len(c) < store.CHUNK_MIN_CHARS]
        self.assertEqual(len(small), 0, f"仍存在过短碎片块：{small}")

    def test_body_text_survives_chunking(self) -> None:
        """正文必须留在块里（不能只留下标题）。"""
        joined = "\n".join(store._chunk(DOC))
        self.assertIn("联合切割组件 CUT", joined)
        self.assertIn("时间差需在配置窗口内", joined)

    def test_long_section_keeps_heading_in_each_piece(self) -> None:
        body = "\n\n".join(f"第{i}段。" + "内容" * 120 for i in range(4))
        chunks = store._chunk(f"# 大节\n\n{body}")
        long_chunks = [c for c in chunks if len(c) > store.CHUNK_MIN_CHARS]
        self.assertGreaterEqual(len(long_chunks), 2, "超长小节应被切开")
        for chunk in long_chunks:
            self.assertTrue(chunk.startswith("# 大节"), f"切片应带标题上下文：{chunk[:20]!r}")

    def test_short_document_is_kept_whole(self) -> None:
        chunks = store._chunk("# 小文档\n\n只有一句话。")
        self.assertEqual(len(chunks), 1)
        self.assertIn("只有一句话", chunks[0])


class FingerprintTests(unittest.TestCase):
    def test_chunker_version_participates_in_fingerprint(self) -> None:
        """改了分块逻辑必须能触发重建，否则旧碎片索引会一直用下去。"""
        with TemporaryDirectory() as tmp:
            docs = Path(tmp)
            (docs / "a.md").write_text("# t\n\n正文\n", encoding="utf-8")
            before = store._fingerprint(docs)
            original = store.CHUNKER_VERSION
            try:
                store.CHUNKER_VERSION = original + "-changed"
                self.assertNotEqual(before, store._fingerprint(docs))
            finally:
                store.CHUNKER_VERSION = original


class KeywordFallbackTests(unittest.TestCase):
    def test_keyword_hits_skip_tiny_fragments(self) -> None:
        with TemporaryDirectory() as tmp:
            docs = Path(tmp)
            (docs / "doc.md").write_text(DOC, encoding="utf-8")
            original = store._docs_dir
            try:
                store._docs_dir = lambda system_id: docs  # type: ignore[assignment]
                hits = store._keyword_hits("切割 天区 局部图", "3")
            finally:
                store._docs_dir = original  # type: ignore[assignment]
        self.assertTrue(hits, "关键词检索应命中正文")
        for hit in hits:
            self.assertGreaterEqual(
                len(hit["snippet"]), store.RETRIEVAL_MIN_CHARS,
                f"不应返回过短碎片：{hit['snippet']!r}",
            )


class SubAgentDepsTests(unittest.TestCase):
    def _ctx(self, **overrides) -> types.SimpleNamespace:
        deps = AgentDeps(
            descriptor={"name": "demo", "services": []},
            question="主问题",
            knowledge_context="[知识库 | 多星联合分析流程.md]\n## 3. 总体业务流程\n联合切割组件 CUT …",
            clock_hint="## 当前时间\n（当前时间：2026-10-13 10:00）",
            **overrides,
        )
        return types.SimpleNamespace(deps=deps)

    def test_sub_deps_carries_knowledge_context(self) -> None:
        """子代理必须拿到知识库（否则委派路径下看不到文档）。"""
        sub = _sub_deps(self._ctx(), "子任务")
        self.assertEqual(sub.knowledge_context, self._ctx().deps.knowledge_context)
        self.assertEqual(sub.question, "子任务")

    def test_sub_deps_carries_clock_hint(self) -> None:
        self.assertTrue(_sub_deps(self._ctx(), "子任务").clock_hint)


if __name__ == "__main__":
    unittest.main()

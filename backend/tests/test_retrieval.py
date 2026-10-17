"""检索链路（BM25 + RRF）与多轮 Query Rewrite 的单元测试。

这些测试**不碰数据库**：
  • bm25.rank / fusion.rrf_fuse / rewrite_query 都是纯函数；
  • store.search_knowledge_hits 的集成用例用 monkeypatch 注入两路召回结果。
"""
from collections import Counter
from unittest import mock

import pytest

from app.agent.diagnostics.knowledge import bm25, fusion, store
from app.agent.diagnostics.knowledge.rewrite import needs_rewrite, rewrite_query


# ── 分词 ──────────────────────────────────────────────────────

def test_tokenize_keeps_chinese_terms_and_digits():
    tokens = bm25.tokenize("Redis 内存 8MB 打满")
    assert tokens == ["redis", "内存", "8mb", "打满"]


def test_tokenize_drops_single_char_stopwords():
    assert bm25.tokenize("的 内存") == ["内存"]


def test_tokenize_keeps_port_numbers():
    assert "3306" in bm25.tokenize("端口 3306 连接被拒绝")


# ── BM25 ─────────────────────────────────────────────────────

def _corpus(texts: list[str]):
    docs, df = [], Counter()
    for index, text in enumerate(texts):
        tf = Counter(bm25.tokenize(text))
        docs.append({"source": f"doc{index}.md", "content": text, "tf": tf, "dl": sum(tf.values())})
        df.update(tf.keys())
    avgdl = sum(doc["dl"] for doc in docs) / len(docs)
    return docs, df, avgdl


def test_bm25_prefers_exact_term_match():
    docs, df, avgdl = _corpus([
        "Redis 内存打满，evicted_keys 持续增长，缓存命中率下降",
        "Kafka 消费组 lag 很高，需要扩容消费者",
    ])
    hits = bm25.rank("evicted_keys", docs, df, avgdl)
    assert hits, "精确词面必须能命中"
    assert "evicted_keys" in hits[0]["snippet"]
    assert hits[0]["score"] == 1.0, "最高分归一到 1.0，便于与记忆分数合并排序"
    assert hits[0]["retriever"] == "bm25"


def test_bm25_returns_empty_without_match():
    docs, df, avgdl = _corpus(["完全无关的文档内容"])
    assert bm25.rank("port 8080 timeout", docs, df, avgdl) == []


# ── RRF ──────────────────────────────────────────────────────

def _hit(snippet: str, name: str = "doc.md", score: float = 0.5, retriever: str = "vector") -> dict:
    return {"name": name, "snippet": snippet, "score": score, "retriever": retriever}


def test_rrf_merges_same_chunk_from_two_retrievers():
    snippet = "Redis 内存打满 evicted_keys 增长"
    vector = [_hit("向量独有的一段", name="a.md"), _hit(snippet, name="redis.md", score=0.81)]
    keyword = [_hit(snippet, name="redis.md", score=1.0, retriever="bm25"), _hit("BM25 独有的一段", name="b.md", retriever="bm25")]

    fused = fusion.rrf_fuse(vector, keyword, limit=10)

    assert fused[0]["snippet"] == snippet, "两路都命中的块应排第一"
    assert fused[0]["retrievers"] == ["bm25", "vector"], "应记录命中的召回路径"
    assert fused[0]["score"] == 1.0, "最高 RRF 分归一到 1.0"
    assert len(fused) == 3, "同一块不应重复出现"


def test_rrf_empty_inputs():
    assert fusion.rrf_fuse([], []) == []


# ── Query Rewrite ────────────────────────────────────────────

HISTORY = "上一轮问题: Kafka 消费组 lag 很高怎么办\n上一轮结论: 建议先扩容消费者"


def test_short_followup_is_rewritten_with_previous_question():
    assert needs_rewrite("那怎么办")
    rewritten = rewrite_query("那怎么办", HISTORY)
    assert "Kafka 消费组 lag" in rewritten and "那怎么办" in rewritten


def test_long_complete_question_is_untouched():
    question = "帮我查一下 MySQL 的连接数是否打满，以及最近有没有慢查询"
    assert rewrite_query(question, HISTORY) == question


def test_rewrite_without_history_is_noop():
    assert rewrite_query("那怎么办", "") == "那怎么办"


def test_rewrite_skips_when_already_contains_previous():
    question = "Kafka 消费组 lag 很高怎么办，现在还是吗"
    assert rewrite_query(question, HISTORY) == question


def test_rewrite_can_be_disabled():
    assert rewrite_query("那怎么办", HISTORY, enabled=False) == "那怎么办"


# ── store 集成：三路召回 → RRF → rerank ──────────────────────

def test_search_uses_rrf_for_vector_and_bm25(monkeypatch):
    vector = [_hit("语义命中的段落", name="v.md", score=0.72)]
    keyword = [_hit("包含 3306 端口被拒绝", name="k.md", score=1.0)]

    monkeypatch.setattr(store, "ensure_index", lambda *_a, **_k: True)
    monkeypatch.setattr(store, "_vector_hits", lambda *_a, **_k: vector)
    monkeypatch.setattr(store, "bm25_hits", lambda *_a, **_k: keyword)
    monkeypatch.setattr(store, "rerank_documents", lambda _q, hits: hits)
    monkeypatch.setattr(store, "_keyword_hits", lambda *_a, **_k: pytest.fail("不应走关键词兜底"))

    hits = store.search_knowledge_hits("3306 端口", "3", max_chars=4000)

    assert len(hits) == 2
    assert all(hit.get("rrf_score") is not None for hit in hits), "两路都应有 RRF 分"


def test_search_falls_back_to_keyword_when_both_retrievers_empty(monkeypatch):
    fallback = [_hit("关键词兜底命中", name="f.md", score=2.0)]

    monkeypatch.setattr(store, "ensure_index", lambda *_a, **_k: False)
    monkeypatch.setattr(store, "bm25_hits", lambda *_a, **_k: [])
    monkeypatch.setattr(store, "_keyword_hits", lambda *_a, **_k: fallback)
    monkeypatch.setattr(store, "rerank_documents", lambda _q, hits: hits)

    hits = store.search_knowledge_hits("随便问问", "3", max_chars=4000)

    assert hits and hits[0]["snippet"] == "关键词兜底命中"


def test_search_still_works_when_embedding_unavailable(monkeypatch):
    """embedding 挂了（_sync_index=False）时，BM25 仍应能出结果。"""
    keyword = [_hit("Redis 内存打满，evicted_keys 增长", name="k.md", score=1.0)]

    monkeypatch.setattr(store, "ensure_index", lambda *_a, **_k: False)
    monkeypatch.setattr(store, "bm25_hits", lambda *_a, **_k: keyword)
    monkeypatch.setattr(store, "_keyword_hits", lambda *_a, **_k: pytest.fail("有 BM25 结果时不应兜底"))
    monkeypatch.setattr(store, "rerank_documents", lambda _q, hits: hits)

    hits = store.search_knowledge_hits("evicted_keys", "3", max_chars=4000)

    assert hits and "evicted_keys" in hits[0]["snippet"]


# ── 索引过期不得阻塞检索（B2）────────────────────────────────

def test_stale_index_does_not_block_retrieval(monkeypatch, tmp_path):
    """文档变更后，检索路径不能等同步完成；应先用旧索引/BM25 回答，后台刷新。"""
    import threading
    import time

    docs = tmp_path / "3"
    docs.mkdir()
    (docs / "a.md").write_text("# t\n\n正文", encoding="utf-8")

    started, finished = threading.Event(), threading.Event()

    def slow_sync(_sid):
        started.set()
        time.sleep(0.4)
        finished.set()
        return True

    monkeypatch.setattr(store, "_docs_dir", lambda _sid: docs)
    monkeypatch.setattr(store, "_fingerprint_cache", {})          # 缓存为空 → 视为过期
    monkeypatch.setattr(store, "_sync_blocked_until", {})
    monkeypatch.setattr(store, "_syncing", set())
    monkeypatch.setattr(store, "_sync_index", slow_sync)
    monkeypatch.setattr(store, "_has_chunks", lambda _sid: True)  # 有旧索引可用

    began = time.monotonic()
    usable = store.ensure_index("3")
    elapsed = time.monotonic() - began

    assert usable is True, "有旧索引时应立即可用"
    assert elapsed < 0.3, f"检索路径不应等待同步（实际 {elapsed:.2f}s）"
    assert started.wait(2.0), "应触发后台同步"
    assert finished.wait(3.0)


def test_ensure_index_wait_true_syncs_inline(monkeypatch, tmp_path):
    docs = tmp_path / "3"
    docs.mkdir()
    (docs / "a.md").write_text("# t\n\n正文", encoding="utf-8")
    calls = []

    monkeypatch.setattr(store, "_docs_dir", lambda _sid: docs)
    monkeypatch.setattr(store, "_fingerprint_cache", {})
    monkeypatch.setattr(store, "_sync_blocked_until", {})
    monkeypatch.setattr(store, "_sync_index", lambda sid: calls.append(sid) or True)

    assert store.ensure_index("3", wait=True) is True
    assert calls == ["3"], "wait=True 应同步刷新（预热/排障用）"


def test_ensure_index_respects_failure_cooldown(monkeypatch, tmp_path):
    """同步失败后的冷却期内不再反复重试（避免每次检索都卡）。"""
    import time

    docs = tmp_path / "3"
    docs.mkdir()
    (docs / "a.md").write_text("# t\n\n正文", encoding="utf-8")

    monkeypatch.setattr(store, "_docs_dir", lambda _sid: docs)
    monkeypatch.setattr(store, "_fingerprint_cache", {})
    monkeypatch.setattr(store, "_sync_blocked_until", {"3": time.monotonic() + 60})
    monkeypatch.setattr(store, "_has_chunks", lambda _sid: True)
    monkeypatch.setattr(store, "_sync_index", lambda _sid: pytest.fail("冷却期内不应重试同步"))

    assert store.ensure_index("3") is True

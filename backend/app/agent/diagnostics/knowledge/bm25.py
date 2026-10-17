"""BM25 关键词召回：补向量检索对"精确信息"的不稳。

为什么需要：纯向量对端口、错误码、专有名词、告警编号这类**精确信息**召回不稳
（语义相近但数字不同也会被召回）。BM25 走词面匹配，正好互补。

实现要点：
  • 中文用 jieba（search 模式）分词，数字/英文 token 原样保留；
  • 语料直接读 knowledge_chunks（与向量检索同源，不额外维护索引）；
  • 索引按 system 缓存（TTL 60s），规模在几百块量级，内存 BM25 足够；
  • 只返回**名次**给 RRF 用（见 fusion.py），分数尺度不与向量比较。
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import Counter

import jieba

log = logging.getLogger(__name__)

K1 = 1.5
B = 0.75
CACHE_TTL = 60.0

# system_id -> (built_at, docs, df, avgdl)
_cache: dict[int, tuple[float, list[dict], Counter, float]] = {}
_lock = threading.Lock()


def tokenize(text: str) -> list[str]:
    """jieba search 分词；丢弃单字虚词，保留数字与英文词。"""
    out: list[str] = []
    for raw in jieba.lcut_for_search((text or "").lower()):
        token = raw.strip()
        if not token:
            continue
        if token.isdigit() or len(token) >= 2:
            out.append(token)
    return out


def _load(system_id: int) -> tuple[list[dict], Counter, float]:
    from sqlalchemy import text

    from app.core.database import engine

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT source, content FROM knowledge_chunks WHERE system_id = :sid"),
            {"sid": system_id},
        ).all()

    docs: list[dict] = []
    df: Counter = Counter()
    for source, content in rows:
        tf = Counter(tokenize(content))
        if not tf:
            continue
        docs.append({"source": source, "content": content, "tf": tf, "dl": sum(tf.values())})
        df.update(tf.keys())          # 每个 token 每篇只计一次 → document frequency
    avgdl = (sum(doc["dl"] for doc in docs) / len(docs)) if docs else 0.0
    return docs, df, avgdl


def _index(system_id: int) -> tuple[list[dict], Counter, float]:
    now = time.monotonic()
    with _lock:
        cached = _cache.get(system_id)
        if cached and now - cached[0] < CACHE_TTL:
            return cached[1], cached[2], cached[3]
    docs, df, avgdl = _load(system_id)
    with _lock:
        _cache[system_id] = (now, docs, df, avgdl)
    return docs, df, avgdl


def invalidate(system_id: str | int | None = None) -> None:
    """文档/索引变更后清缓存（与向量索引的 invalidate 一起调用）。"""
    with _lock:
        if system_id is None:
            _cache.clear()
        else:
            try:
                _cache.pop(int(system_id), None)
            except (TypeError, ValueError):
                pass


def rank(
    query: str,
    docs: list[dict],
    df: Counter,
    avgdl: float,
    limit: int = 16,
) -> list[dict]:
    """纯函数：对给定语料做 BM25 排序，返回命中（score 归一到 1.0）。"""
    tokens = set(tokenize(query))
    if not docs or not tokens:
        return []

    total = len(docs)
    scored: list[tuple[float, dict]] = []
    for doc in docs:
        score = 0.0
        for token in tokens:
            tf = doc["tf"].get(token, 0)
            if not tf:
                continue
            idf = math.log(1 + (total - df.get(token, 0) + 0.5) / (df.get(token, 0) + 0.5))
            denom = tf + K1 * (1 - B + B * (doc["dl"] / (avgdl or 1.0)))
            score += idf * tf * (K1 + 1) / denom
        if score > 0:
            scored.append((score, doc))

    scored.sort(key=lambda item: -item[0])
    top = scored[:limit]
    if not top:
        return []
    best = top[0][0] or 1.0
    return [
        {
            "name": doc["source"],
            "snippet": doc["content"],
            "score": round(score / best, 4),
            "retriever": "bm25",
        }
        for score, doc in top
    ]


def bm25_hits(query: str, system_id: str | int, limit: int = 16) -> list[dict]:
    """BM25 召回（带进程内缓存）；任何异常都只降级为空，不影响主检索。"""
    try:
        sid = int(system_id)
    except (TypeError, ValueError):
        return []
    try:
        docs, df, avgdl = _index(sid)
    except Exception as exc:  # noqa: BLE001 - 关键词路失败不应让检索整体失败
        log.warning("[bm25] 索引加载失败（跳过关键词召回）: %s", exc)
        return []
    return rank(query, docs, df, avgdl, limit=limit)

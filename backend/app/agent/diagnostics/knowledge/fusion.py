"""RRF（Reciprocal Rank Fusion）：按**名次**融合多路召回。

为什么不直接加权分数：向量余弦（0~1）与 BM25（无上界）尺度不可比，调权重既脆又不通用。
RRF 只用名次，score = Σ 1/(k + rank)，k=60 是常用默认值，对两路召回都不需要调参。

归一化：融合后把最高分缩到 1.0，便于与"结构化记忆"的分数一起排序（见 store.search_with_memories）。
"""
from __future__ import annotations

import hashlib

RRF_K = 60


def _key(hit: dict) -> str:
    """同一块内容在不同召回路径里的身份：按正文哈希（与 _dedupe_hits 的语义一致）。"""
    return hashlib.md5((hit.get("snippet") or "").strip().encode("utf-8")).hexdigest()


def rrf_fuse(*ranked_lists: list[dict], k: int = RRF_K, limit: int | None = None) -> list[dict]:
    """融合若干**已按相关性降序**的召回列表，返回按 RRF 分数降序的命中。"""
    scores: dict[str, float] = {}
    first_seen: dict[str, dict] = {}
    retrievers: dict[str, set[str]] = {}

    for hits in ranked_lists:
        for rank, hit in enumerate(hits or [], start=1):
            key = _key(hit)
            first_seen.setdefault(key, hit)
            retrievers.setdefault(key, set()).add(hit.get("retriever", "vector"))
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)

    if not scores:
        return []

    ordered = sorted(scores.items(), key=lambda item: -item[1])
    best = ordered[0][1] or 1.0
    out: list[dict] = []
    for key, score in ordered[: (limit or len(ordered))]:
        hit = dict(first_seen[key])
        hit["rrf_score"] = round(score, 6)
        hit["score"] = round(score / best, 4)   # 归一到 (0, 1]
        hit["retrievers"] = sorted(retrievers[key])
        out.append(hit)
    return out

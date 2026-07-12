"""
RAG 知识库 —— API Embedding + JSON 向量存储，零额外依赖。

设计：
  • Embedding 调 OpenAI 兼容接口（默认复用 deepseek_api_key / deepseek_base_url）
  • 向量存 JSON 文件（docs/<system_id>/vectors.json），每个系统独立
  • 余弦相似度纯 Python 实现，无 numpy
  • 文档变更时自动重建索引（mtime 指纹）
  • Embedding API 失败时自动降级关键词检索
"""
import hashlib
import json
import logging
import math
import os
import re
import threading
from pathlib import Path

import httpx

from ....core.config import settings

log = logging.getLogger(__name__)

DOCS_ROOT = Path(__file__).parent / "docs"
CHUNK_SIZE = 400
CHUNK_OVERLAP = 60
TOP_K = 4
MAX_CHARS = 1500

# 进程内缓存：避免每次请求都读磁盘
_cache: dict[str, dict] = {}   # system_id → {fingerprint, chunks, embeddings}
_lock = threading.Lock()


# ── Embedding API ──────────────────────────────────────────────

def _embed(texts: list[str]) -> list[list[float]] | None:
    """调 OpenAI 兼容 embedding 接口，失败返回 None。"""
    api_key = settings.embedding_api_key or settings.deepseek_api_key
    base_url = (settings.embedding_base_url or settings.deepseek_base_url).rstrip("/")
    model = settings.embedding_model
    if not api_key:
        return None
    try:
        resp = httpx.post(
            f"{base_url}/v1/embeddings",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "input": texts},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return [item["embedding"] for item in data["data"]]
    except Exception as exc:
        log.warning(f"[rag] embedding API 失败: {exc}")
        return None


# ── 余弦相似度（纯 Python）──────────────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


# ── 文本分块 ──────────────────────────────────────────────────

def _chunk(text: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks = []
    for para in paras:
        if len(para) <= CHUNK_SIZE:
            chunks.append(para)
        else:
            start = 0
            while start < len(para):
                chunks.append(para[start: start + CHUNK_SIZE])
                start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


# ── 指纹（文档变更检测）──────────────────────────────────────

def _fingerprint(docs_dir: Path) -> str:
    h = hashlib.md5()
    for f in sorted(docs_dir.glob("*.md")) + sorted(docs_dir.glob("*.txt")):
        h.update(str(f.stat().st_mtime).encode())
    return h.hexdigest()


# ── 索引构建 ─────────────────────────────────────────────────

def _docs_dir(system_id: str) -> Path:
    return DOCS_ROOT / system_id


def _vectors_path(system_id: str) -> Path:
    return _docs_dir(system_id) / "vectors.json"


def _load_cache(system_id: str) -> dict | None:
    """从磁盘加载已有向量缓存，校验指纹是否过期。"""
    vpath = _vectors_path(system_id)
    docs_dir = _docs_dir(system_id)
    if not vpath.exists():
        return None
    try:
        stored = json.loads(vpath.read_text(encoding="utf-8"))
        if stored.get("fingerprint") == _fingerprint(docs_dir):
            return stored
    except Exception:
        pass
    return None


def _build_index(system_id: str) -> dict | None:
    """读文档 → 分块 → embedding → 写 vectors.json。"""
    docs_dir = _docs_dir(system_id)
    if not docs_dir.exists():
        return None

    doc_files = list(docs_dir.glob("*.md")) + list(docs_dir.glob("*.txt"))
    doc_files = [f for f in doc_files if f.name != "vectors.json"]
    if not doc_files:
        return None

    chunks, metas = [], []
    for f in doc_files:
        for chunk in _chunk(f.read_text(encoding="utf-8")):
            chunks.append(chunk)
            metas.append(f.name)

    if not chunks:
        return None

    # 分批 embed（每批最多 20 条，避免超出 API 限制）
    all_embeddings: list[list[float]] = []
    batch_size = 20
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i: i + batch_size]
        vecs = _embed(batch)
        if vecs is None:
            return None      # API 失败，不写缓存
        all_embeddings.extend(vecs)

    data = {
        "fingerprint": _fingerprint(docs_dir),
        "chunks": chunks,
        "metas": metas,
        "embeddings": all_embeddings,
    }
    _vectors_path(system_id).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    log.info(f"[rag] system={system_id} 索引重建完成，共 {len(chunks)} 个块")
    return data


def _get_index(system_id: str) -> dict | None:
    """返回最新索引（命中内存缓存 > 磁盘缓存 > 重新构建）。"""
    with _lock:
        cached = _cache.get(system_id)
        if cached:
            # 校验文档有没有改动
            docs_dir = _docs_dir(system_id)
            if docs_dir.exists() and cached.get("fingerprint") == _fingerprint(docs_dir):
                return cached

        data = _load_cache(system_id) or _build_index(system_id)
        if data:
            _cache[system_id] = data
        return data


# ── 公开接口 ──────────────────────────────────────────────────

def get_relevant_context(query: str, system_id: str, max_chars: int = MAX_CHARS) -> str:
    """语义检索知识库，返回相关段落拼接文本；无结果或失败返回空字符串。"""
    hits = search_knowledge_hits(query, system_id, max_chars=max_chars)
    if not hits:
        return ""
    return "\n\n".join(hit["snippet"] for hit in hits)


def search_knowledge_hits(query: str, system_id: str, max_chars: int = MAX_CHARS) -> list[dict]:
    """检索知识库，返回 [{name, snippet, score}]，按相关度排序、同文档去重。"""
    index = _get_index(system_id)
    if index:
        hits = _semantic_hits(query, index, max_chars)
        if hits:
            return _dedupe_hits(hits)
    hits = _keyword_hits(query, system_id, max_chars)
    return _dedupe_hits(hits)


def _dedupe_hits(hits: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for hit in sorted(hits, key=lambda item: item.get("score", 0), reverse=True):
        name = hit.get("name") or ""
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(hit)
    return result


def _semantic_hits(query: str, index: dict, max_chars: int) -> list[dict]:
    query_vec = _embed([query])
    if query_vec is None:
        return []

    qv = query_vec[0]
    scored = sorted(
        ((i, _cosine(qv, ev)) for i, ev in enumerate(index["embeddings"])),
        key=lambda x: x[1],
        reverse=True,
    )

    hits: list[dict] = []
    total = 0
    for idx, score in scored[:TOP_K * 2]:
        if score < 0.3:
            break
        chunk = index["chunks"][idx]
        if total + len(chunk) > max_chars:
            break
        hits.append(
            {
                "name": index["metas"][idx],
                "snippet": chunk,
                "score": round(score, 4),
            }
        )
        total += len(chunk)
    return hits


def _keyword_hits(query: str, system_id: str, max_chars: int) -> list[dict]:
    docs_dir = _docs_dir(system_id)
    if not docs_dir.exists():
        return []
    keywords = set(re.findall(r"[\w一-鿿]+", query.lower()))
    if not keywords:
        return []

    scored: list[tuple[int, str, str]] = []
    for file_path in list(docs_dir.glob("*.md")) + list(docs_dir.glob("*.txt")):
        if file_path.name == "vectors.json":
            continue
        content = file_path.read_text(encoding="utf-8")
        for para in [p.strip() for p in re.split(r"\n{2,}", content) if p.strip()]:
            score = sum(1 for kw in keywords if kw in para.lower())
            if score:
                scored.append((score, file_path.name, para))

    scored.sort(key=lambda item: item[0], reverse=True)
    hits: list[dict] = []
    total = 0
    for score, name, chunk in scored[:8]:
        if total + len(chunk) > max_chars:
            break
        hits.append({"name": name, "snippet": chunk, "score": float(score)})
        total += len(chunk)
    return hits


def append_runbook_entry(system_id: str, entry: str) -> None:
    """向该系统的 runbook.md 追加一条故障记录，并清除内存缓存触发重建。"""
    docs_dir = _docs_dir(system_id)
    docs_dir.mkdir(parents=True, exist_ok=True)
    runbook = docs_dir / "runbook.md"
    if not runbook.exists():
        runbook.write_text("# 故障修复经验库\n\n", encoding="utf-8")
    with runbook.open("a", encoding="utf-8") as f:
        f.write(f"\n{entry}\n")
    # 清除缓存，下次访问触发重建
    invalidate_index(system_id)
    log.info(f"[rag] system={system_id} runbook 已更新")


def list_docs(system_id: str) -> list[str]:
    """返回该系统知识库中的文档文件名列表。"""
    docs_dir = _docs_dir(system_id)
    if not docs_dir.exists():
        return []
    return [f.name for f in docs_dir.iterdir()
            if f.suffix in (".md", ".txt") and f.name != "vectors.json"]


def read_doc(system_id: str, name: str) -> str | None:
    """读取单个知识文档内容；不存在时返回 None。"""
    path = _docs_dir(system_id) / Path(name).name
    if not path.exists() or path.suffix not in (".md", ".txt"):
        return None
    return path.read_text(encoding="utf-8")


def invalidate_index(system_id: str) -> None:
    """文档变更后清除内存缓存，下次检索时重建索引。"""
    with _lock:
        _cache.pop(system_id, None)


def _keyword_fallback(query: str, system_id: str, max_chars: int) -> str:
    """降级：关键词匹配，不依赖 embedding API。"""
    docs_dir = _docs_dir(system_id)
    if not docs_dir.exists():
        return ""
    keywords = set(re.findall(r"[\w一-鿿]+", query.lower()))
    if not keywords:
        return ""
    scored = []
    for f in list(docs_dir.glob("*.md")) + list(docs_dir.glob("*.txt")):
        content = f.read_text(encoding="utf-8")
        for para in [p.strip() for p in re.split(r"\n{2,}", content) if p.strip()]:
            score = sum(1 for kw in keywords if kw in para.lower())
            if score:
                scored.append((score, para))
    scored.sort(reverse=True)
    result, total = [], 0
    for _, chunk in scored[:6]:
        if total + len(chunk) > max_chars:
            break
        result.append(chunk)
        total += len(chunk)
    return "\n\n".join(result)

"""
RAG 知识库 —— pgvector 向量检索（生产实现） + 关键词降级。

架构：
  • 文档事实源仍是磁盘 docs/<system_id>/（md/txt），list_docs / read_doc / runbook 追加不变
  • 索引持久化在 Postgres knowledge_chunks 表（pgvector + HNSW 余弦索引）
  • 按 mtime 指纹检测文档变更，自动全量重建该系统分块（幂等 upsert，单事务）
  • Embedding API 失败 → 语义检索不可用，自动降级关键词检索（不依赖 embedding/向量库）

多租户：检索按 system_id 过滤；system_id 归属校验由上游（require_system）保证。
"""
import hashlib
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import text

from app.agent.llm import embedding_endpoint
from app.core.config import settings

log = logging.getLogger(__name__)

DOCS_ROOT = Path(__file__).parent / "docs"
CHUNK_SIZE = 400
CHUNK_OVERLAP = 60
TOP_K = 4
MAX_CHARS = 1500
MIN_SCORE = 0.3          # 语义命中最低相似度（与旧版一致）

# 进程内指纹缓存：避免每次检索都 stat 磁盘
_fingerprint_cache: dict[str, str] = {}
_lock = threading.Lock()


# ── Embedding API ──────────────────────────────────────────────

def _embed(texts: list[str]) -> list[list[float]] | None:
    """调 OpenAI 兼容 embedding 接口，失败返回 None。"""
    endpoint = embedding_endpoint()
    api_key = endpoint.api_key
    base_url = endpoint.base_url.rstrip("/")
    embeddings_url = (
        f"{base_url}/embeddings"
        if base_url.endswith("/v1")
        else f"{base_url}/v1/embeddings"
    )
    if not api_key:
        return None
    try:
        resp = httpx.post(
            embeddings_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": endpoint.model, "input": texts},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        embeddings = [item["embedding"] for item in data["data"]]
        dim = int(settings.embedding_dim or 0)
        if dim and any(len(v) != dim for v in embeddings):
            log.warning(
                f"[rag] embedding 维度与 EMBEDDING_DIM={dim} 不一致，"
                "请同步修改 EMBEDDING_DIM 并重建索引"
            )
            return None
        return embeddings
    except Exception as exc:
        log.warning(f"[rag] embedding API 失败: {exc}")
        return None


def _vec_literal(vec: list[float]) -> str:
    """pgvector 文本字面量 '[0.1,0.2,...]'。"""
    return "[" + ",".join(f"{v:.7g}" for v in vec) + "]"


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


def _docs_dir(system_id: str) -> Path:
    return DOCS_ROOT / system_id


def _as_int_system_id(system_id: str | int) -> int | None:
    """知识库统一用数字 system_id 作为主键；非数字（历史 descriptor id）返回 None。"""
    try:
        return int(system_id)
    except (TypeError, ValueError):
        return None


# ── 索引构建（同步到 PG）─────────────────────────────────────

def _sync_index(system_id: str) -> bool:
    """指纹变更时把磁盘文档分块+embedding 同步进 knowledge_chunks。成功返回 True。"""
    docs_dir = _docs_dir(system_id)
    if not docs_dir.exists() or _as_int_system_id(system_id) is None:
        return False
    fingerprint = _fingerprint(docs_dir)
    with _lock:
        if _fingerprint_cache.get(system_id) == fingerprint:
            return True

    files = [f for f in sorted(docs_dir.glob("*.md")) + sorted(docs_dir.glob("*.txt"))
             if f.name != "vectors.json"]
    chunks: list[tuple[str, str, int]] = []   # (source, content, index)
    for f in files:
        for i, chunk in enumerate(_chunk(f.read_text(encoding="utf-8"))):
            chunks.append((f.name, chunk, i))
    if not chunks:
        return False

    embeddings = _embed([c[1] for c in chunks])
    if embeddings is None:
        return False        # embedding 不可用：保留旧索引，检索端走关键词降级

    try:
        _write_chunks(system_id, fingerprint, chunks, embeddings)
    except Exception as exc:
        log.warning(f"[rag] 向量索引写入失败: {exc}")
        return False
    log.info(f"[rag] system={system_id} pgvector 索引同步完成，共 {len(chunks)} 块")
    return True


def _write_chunks(system_id: str, fingerprint: str,
                  chunks: list[tuple[str, str, int]], embeddings: list[list[float]]) -> None:
    from app.core.database import engine

    sid = int(system_id)
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:   # 单事务：清旧 + 写新 + 更新指纹
        old = conn.execute(
            text("SELECT fingerprint FROM knowledge_index_meta WHERE system_id = :sid"),
            {"sid": sid},
        ).first()
        if old and old.fingerprint != fingerprint:
            conn.execute(
                text("DELETE FROM knowledge_chunks WHERE system_id = :sid"), {"sid": sid}
            )
        for (source, content, index), vec in zip(chunks, embeddings):
            conn.execute(
                text("""
                    INSERT INTO knowledge_chunks
                        (system_id, source, chunk_index, content, content_hash, embedding, updated_at)
                    VALUES
                        (:sid, :source, :chunk_index, :content, :content_hash,
                         CAST(:embedding AS vector), :updated_at)
                    ON CONFLICT (system_id, source, chunk_index) DO UPDATE SET
                        content = EXCLUDED.content,
                        content_hash = EXCLUDED.content_hash,
                        embedding = EXCLUDED.embedding,
                        updated_at = EXCLUDED.updated_at
                """),
                {
                    "sid": sid,
                    "source": source,
                    "chunk_index": index,
                    "content": content,
                    "content_hash": hashlib.md5(content.encode()).hexdigest(),
                    "embedding": _vec_literal(vec),
                    "updated_at": now,
                },
            )
        conn.execute(
            text("""
                INSERT INTO knowledge_index_meta (system_id, fingerprint, chunk_count, synced_at)
                VALUES (:sid, :fp, :cnt, :now)
                ON CONFLICT (system_id) DO UPDATE SET
                    fingerprint = EXCLUDED.fingerprint,
                    chunk_count = EXCLUDED.chunk_count,
                    synced_at = EXCLUDED.synced_at
            """),
            {"sid": sid, "fp": fingerprint, "cnt": len(chunks), "now": now},
        )
    with _lock:
        _fingerprint_cache[system_id] = fingerprint


# ── 公开接口（签名与旧版一致）────────────────────────────────

def get_relevant_context(query: str, system_id: str, max_chars: int = MAX_CHARS) -> str:
    """语义检索知识库，返回相关段落拼接文本；无结果或失败返回空字符串。"""
    hits = search_knowledge_hits(query, system_id, max_chars=max_chars)
    if not hits:
        return ""
    return "\n\n".join(hit["snippet"] for hit in hits)


def search_knowledge_hits(query: str, system_id: str, max_chars: int = MAX_CHARS) -> list[dict]:
    """检索知识库，返回 [{name, snippet, score}]，按相关度排序、同文档去重。"""
    if _sync_index(system_id):
        hits = _vector_hits(query, system_id, max_chars)
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


def _vector_hits(query: str, system_id: str, max_chars: int) -> list[dict]:
    """pgvector 语义检索（余弦距离 = 1 - 相似度）。失败返回空触发关键词降级。"""
    from app.core.database import engine

    try:
        qv = _embed([query])
        if not qv:
            return []
        sid = _as_int_system_id(system_id)
        if sid is None:
            return []
        with engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT source, content,
                           embedding <=> CAST(:qv AS vector) AS distance
                    FROM knowledge_chunks
                    WHERE system_id = :sid
                    ORDER BY embedding <=> CAST(:qv AS vector)
                    LIMIT :k
                """),
                {"qv": _vec_literal(qv[0]), "sid": sid, "k": TOP_K * 2},
            ).all()
        hits: list[dict] = []
        total = 0
        for source, content, distance in rows:
            score = round(1.0 - float(distance), 4)
            if score < MIN_SCORE:
                break
            if total + len(content) > max_chars:
                break
            hits.append({"name": source, "snippet": content, "score": score})
            total += len(content)
        return hits
    except Exception as exc:
        log.warning(f"[rag] pgvector 检索失败: {exc}")
        return []


def _keyword_hits(query: str, system_id: str, max_chars: int) -> list[dict]:
    """降级：关键词匹配磁盘文档，不依赖 embedding API 与向量库。"""
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
    """向该系统的 runbook.md 追加一条故障记录，并触发索引重建。"""
    docs_dir = _docs_dir(system_id)
    docs_dir.mkdir(parents=True, exist_ok=True)
    runbook = docs_dir / "runbook.md"
    if not runbook.exists():
        runbook.write_text("# 故障修复经验库\n\n", encoding="utf-8")
    with runbook.open("a", encoding="utf-8") as f:
        f.write(f"\n{entry}\n")
    # 文档已变：清指纹缓存，下次检索自动重建向量索引
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
    """文档变更后清除指纹缓存，下次检索时同步重建向量索引。"""
    with _lock:
        _fingerprint_cache.pop(system_id, None)


def _keyword_fallback(query: str, system_id: str, max_chars: int) -> str:
    """兼容保留：旧版降级入口，等价于关键词检索拼接。"""
    return "\n\n".join(h["snippet"] for h in _keyword_hits(query, system_id, max_chars))

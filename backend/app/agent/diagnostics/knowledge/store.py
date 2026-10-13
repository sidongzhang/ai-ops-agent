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
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import text

from app.agent.llm import embedding_endpoint
from app.core.config import settings
from .rerank import rerank_documents

log = logging.getLogger(__name__)

DOCS_ROOT = Path(__file__).parent / "docs"
CHUNK_SIZE = 400
CHUNK_OVERLAP = 60
CHUNK_MIN_CHARS = 120    # 过短的片段（标题碎片）会被合并进相邻块，不单独进索引
RETRIEVAL_MIN_CHARS = 60  # 检索时过滤过短块（兜底：旧索引里的碎片块）
CHUNKER_VERSION = "2026-10-13-section-aware-v1"  # 参与指纹：改分块逻辑必须能触发重建
TOP_K = 6
MAX_CHARS = 3000
MIN_SCORE = 0.3          # 语义命中最低相似度（与旧版一致）
RERANK_POOL = 16         # 重排前的候选池大小
EMBED_BATCH = 16         # 单次 embedding 请求的最大文本数（避免大请求超时）
SYNC_FAIL_COOLDOWN = 120.0  # 索引同步失败后的冷却秒数（避免每次检索都卡住重试）

_HEADING_RE = re.compile(r"^#{1,6}\s+\S")

# 进程内缓存：指纹 / 同步失败冷却
_fingerprint_cache: dict[str, str] = {}
_sync_blocked_until: dict[str, float] = {}
_lock = threading.Lock()


# ── Embedding API ──────────────────────────────────────────────

def _embed(texts: list[str]) -> list[list[float]] | None:
    """调 OpenAI 兼容 embedding 接口（分批，避免大请求超时），失败返回 None。"""
    if not texts:
        return []
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
    out: list[list[float]] = []
    try:
        for start in range(0, len(texts), EMBED_BATCH):
            batch = texts[start:start + EMBED_BATCH]
            resp = httpx.post(
                embeddings_url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": endpoint.model, "input": batch},
                timeout=30,
            )
            resp.raise_for_status()
            out.extend(item["embedding"] for item in resp.json()["data"])
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[rag] embedding API 失败: {exc}")
        return None
    dim = int(settings.embedding_dim or 0)
    if dim and any(len(v) != dim for v in out):
        log.warning(
            f"[rag] embedding 维度与 EMBEDDING_DIM={dim} 不一致，"
            "请同步修改 EMBEDDING_DIM 并重建索引"
        )
        return None
    return out


def _vec_literal(vec: list[float]) -> str:
    """pgvector 文本字面量 '[0.1,0.2,...]'。"""
    return "[" + ",".join(f"{v:.7g}" for v in vec) + "]"


# ── 文本分块 ──────────────────────────────────────────────────

def _chunk(text: str) -> list[str]:
    """按 markdown **分节**切块，并把过短片段合并。

    为什么不用"按空行切"：markdown 的**标题行本身就自成一段**，按空行切会让标题变成
    独立小块。实测 system 3 的索引 351 块里 **172 块不足 20 字**（"约束与规则："之类），
    而短文本的向量相似度又容易偏高 → 把真正的正文块挤出 top-k，
    表现为「检索到了却答不出」（报告 #273：注入 180 字全是标题碎片）。

    现在的做法：
      1) 以标题为界分组，**标题与其后的正文绑在同一组**；
      2) 组超长（> CHUNK_SIZE）时按段切开，并给每片**带上标题**（保留语义上下文）；
      3) 仍短于 CHUNK_MIN_CHARS 的相邻片**合并**，不留碎片块。
    """
    groups: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            groups.append(body)

    for line in text.splitlines():
        if _HEADING_RE.match(line.strip()):
            flush()
            buf = [line]
        else:
            buf.append(line)
    flush()

    chunks: list[str] = []
    for group in groups:
        chunks.extend(_split_group(group))
    return _merge_short(chunks)


def _split_group(group: str) -> list[str]:
    """把一组（标题 + 正文）切成不超过 CHUNK_SIZE 的片段，每片都带上标题。"""
    lines = group.splitlines()
    has_heading = bool(lines) and bool(_HEADING_RE.match(lines[0].strip()))
    heading = lines[0].strip() if has_heading else ""
    body = "\n".join(lines[1:] if has_heading else lines).strip()
    if not body:
        return [group.strip()]          # 只有标题（后面可能紧跟别的组）→ 交给合并处理
    if len(group) <= CHUNK_SIZE:
        return [group.strip()]
    pieces: list[str] = []
    for para in [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]:
        if len(para) <= CHUNK_SIZE:
            pieces.append(para)
        else:
            start = 0
            while start < len(para):
                pieces.append(para[start: start + CHUNK_SIZE])
                start += CHUNK_SIZE - CHUNK_OVERLAP
    if heading:
        return [f"{heading}\n{piece}" for piece in pieces]
    return pieces


def _merge_short(chunks: list[str]) -> list[str]:
    """把过短片段与相邻片段合并（避免标题碎片单独成块）。"""
    merged: list[str] = []
    pending = ""
    for chunk in chunks:
        candidate = f"{pending}\n{chunk}".strip() if pending else chunk
        if len(candidate) < CHUNK_MIN_CHARS:
            pending = candidate
            continue
        merged.append(candidate)
        pending = ""
    if pending:
        if merged:
            merged[-1] = f"{merged[-1]}\n{pending}".strip()
        else:
            merged.append(pending)
    return merged


# ── 指纹（文档变更检测）──────────────────────────────────────

def _fingerprint(docs_dir: Path) -> str:
    h = hashlib.md5()
    h.update(CHUNKER_VERSION.encode())   # 分块逻辑变了也要触发重建（否则旧碎片索引会一直用下去）
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
        if _sync_blocked_until.get(system_id, 0.0) > time.monotonic():
            return False        # 上次同步失败，冷却中 → 快速走关键词降级，避免每次卡住

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
        # embedding 不可用：保留旧索引，检索端走关键词降级；并冷却一段时间，避免反复重试
        with _lock:
            _sync_blocked_until[system_id] = time.monotonic() + SYNC_FAIL_COOLDOWN
        return False

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


def get_relevant_context_with_memories(query: str, system_id: str, max_chars: int = MAX_CHARS) -> str:
    """知识库 + 结构化记忆联合版：记忆条目带置信度标注注入上下文。"""
    hits = search_with_memories(query, system_id, max_chars=max_chars)
    if not hits:
        return ""
    parts = []
    for hit in hits:
        tag = "🧠记忆" if hit.get("memory") else "知识库"
        parts.append(f"[{tag} | {hit['name']}]\n{hit['snippet']}")
    return "\n\n".join(parts)


def search_knowledge_hits(query: str, system_id: str, max_chars: int = MAX_CHARS) -> list[dict]:
    """检索知识库 → 去重 → 重排 → 按字符预算裁剪。返回 [{name, snippet, score}]。"""
    hits: list[dict] = []
    if _sync_index(system_id):
        hits = _vector_hits(query, system_id)
    if not hits:
        hits = _keyword_hits(query, system_id)
    hits = _dedupe_hits(hits)
    hits = rerank_documents(query, hits)
    return _apply_budget(hits, max_chars)


def _apply_budget(hits: list[dict], max_chars: int) -> list[dict]:
    """按累计字符预算裁剪（至少保留 1 条）。"""
    out: list[dict] = []
    total = 0
    for hit in hits:
        text = hit.get("snippet") or ""
        if out and total + len(text) > max_chars:
            break
        out.append(hit)
        total += len(text)
    return out


def search_with_memories(query: str, system_id: str, max_chars: int = MAX_CHARS) -> list[dict]:
    """知识库 + 结构化记忆联合检索：记忆按置信度/有效性加权，作为额外命中合并。

    记忆条目标记 source='memory'，name 前缀「记忆」，UI/审计可见来源。
    """
    hits = search_knowledge_hits(query, system_id, max_chars=max_chars)
    try:
        from ...services.knowledge.memory import search_memories

        sid = _as_int_system_id(system_id)
        if sid is None:
            return hits
        memories = search_memories(system_id=sid, query=query, max_results=2)
        for m in memories:
            snippet = f"【{m['validity']}】根因: {m['root_cause']}" + (f" | 处置: {m['remedy']}" if m["remedy"] else "")
            hits.append({
                "name": f"记忆({m['source']}, 置信度{m['confidence']:.1f}) {m['symptom'][:30]}",
                "snippet": snippet[:400],
                "score": m["score"],
                "memory": True,
            })
        hits.sort(key=lambda h: -h.get("score", 0))
    except Exception as exc:  # noqa: BLE001 - 记忆检索失败不影响知识库结果
        log.debug(f"[memory] 合并记忆检索失败: {exc}")
    return hits


def _dedupe_hits(hits: list[dict]) -> list[dict]:
    """按「文档 + 分块内容」去重，而不是按文档名。

    大文档会被切成多个分块、共用同一个 name；若按 name 去重，每份文档只会
    剩下得分最高的那 1 个分块，导致「文档里明明有、却检索不到」。
    """
    seen: set[str] = set()
    result: list[dict] = []
    for hit in sorted(hits, key=lambda item: item.get("score", 0), reverse=True):
        name = hit.get("name") or ""
        snippet = (hit.get("snippet") or "").strip()
        if not name or not snippet:
            continue
        key = f"{name}::{hashlib.md5(snippet.encode('utf-8')).hexdigest()}"
        if key in seen:
            continue
        seen.add(key)
        result.append(hit)
    return result


def _vector_hits(query: str, system_id: str) -> list[dict]:
    """pgvector 语义检索（余弦距离 = 1 - 相似度）。返回候选池，预算留待重排后统一裁。"""
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
                      AND length(content) >= :min_chars
                    ORDER BY embedding <=> CAST(:qv AS vector)
                    LIMIT :k
                """),
                {
                    "qv": _vec_literal(qv[0]),
                    "sid": sid,
                    "k": RERANK_POOL,
                    # 兜底过滤：旧索引里可能还留着"标题碎片"块（分块修复后重建即消失）
                    "min_chars": RETRIEVAL_MIN_CHARS,
                },
            ).all()
        hits: list[dict] = []
        for source, content, distance in rows:
            score = round(1.0 - float(distance), 4)
            if score < MIN_SCORE:
                break
            hits.append({"name": source, "snippet": content, "score": score})
            if len(hits) >= RERANK_POOL:
                break
        return hits
    except Exception as exc:
        log.warning(f"[rag] pgvector 检索失败: {exc}")
        return []


def _keyword_hits(query: str, system_id: str) -> list[dict]:
    """降级：关键词匹配磁盘文档，不依赖 embedding API 与向量库。返回候选池。"""
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
        # 与向量检索用同一套分块（分节 + 合并碎片），避免降级路径又吐出标题碎片
        for para in _chunk(content):
            if len(para) < RETRIEVAL_MIN_CHARS:
                continue
            score = sum(1 for kw in keywords if kw in para.lower())
            if score:
                scored.append((score, file_path.name, para))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {"name": name, "snippet": chunk, "score": float(score)}
        for score, name, chunk in scored[:RERANK_POOL]
    ]


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

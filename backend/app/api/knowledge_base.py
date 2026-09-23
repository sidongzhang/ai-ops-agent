"""System knowledge base management endpoints."""
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session

from ..agent.diagnostics.knowledge.store import (
    _docs_dir,
    get_relevant_context,
    invalidate_index,
    list_docs,
    read_doc,
)
from ..core.database import get_session
from ..core.deps import get_current_org_id, require_operator
from ..schemas import KnowledgeDocCreate, KnowledgeDocDetail, KnowledgeDocOut
from ..services.systems.service import require_system

router = APIRouter(prefix="/systems", tags=["knowledge"])
ALLOWED_SUFFIXES = {".md", ".txt"}
MAX_FILE_SIZE = 2 * 1024 * 1024


def _safe_doc_name(name: str, suffix: str = ".md") -> str:
    raw = Path(name).stem or "knowledge"
    safe = re.sub(r"[^\w\-.\u4e00-\u9fff]+", "-", raw).strip("-._") or "knowledge"
    return f"{safe[:96]}{suffix}"


def _doc_out(system_id: int, name: str) -> KnowledgeDocOut:
    path = _docs_dir(str(system_id)) / name
    return KnowledgeDocOut(name=name, size=path.stat().st_size if path.exists() else 0)


@router.get("/{system_id}/knowledge/docs", response_model=list[KnowledgeDocOut])
def list_knowledge_docs(
    system_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    system = require_system(session, system_id, org_id)
    return [_doc_out(system.id, name) for name in list_docs(str(system.id))]


@router.get("/{system_id}/knowledge/docs/{name}", response_model=KnowledgeDocDetail)
def read_knowledge_doc(
    system_id: int,
    name: str,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    system = require_system(session, system_id, org_id)
    content = read_doc(str(system.id), name)
    if content is None:
        raise HTTPException(404, "文档不存在")
    safe_name = Path(name).name
    return KnowledgeDocDetail(name=safe_name, size=len(content.encode("utf-8")), content=content)


@router.post("/{system_id}/knowledge/docs", response_model=KnowledgeDocOut)
def upsert_knowledge_doc(
    system_id: int,
    body: KnowledgeDocCreate,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    _user=Depends(require_operator),
):
    system = require_system(session, system_id, org_id)
    docs_dir = _docs_dir(str(system.id))
    docs_dir.mkdir(parents=True, exist_ok=True)
    filename = _safe_doc_name(body.name)
    path = docs_dir / filename
    path.write_text(body.content.strip() + "\n", encoding="utf-8")
    invalidate_index(str(system.id))
    return _doc_out(system.id, filename)


@router.post("/{system_id}/knowledge/upload", response_model=list[KnowledgeDocOut])
async def upload_knowledge_docs(
    system_id: int,
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    _user=Depends(require_operator),
):
    system = require_system(session, system_id, org_id)
    docs_dir = _docs_dir(str(system.id))
    docs_dir.mkdir(parents=True, exist_ok=True)
    saved: list[KnowledgeDocOut] = []
    for file in files:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(400, f"仅支持上传 {', '.join(sorted(ALLOWED_SUFFIXES))} 文件")
        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(400, f"文件 {file.filename} 超过 2MB 限制")
        filename = _safe_doc_name(file.filename or "knowledge", suffix)
        path = docs_dir / filename
        path.write_text(content.decode("utf-8", errors="replace").strip() + "\n", encoding="utf-8")
        saved.append(_doc_out(system.id, filename))
    invalidate_index(str(system.id))
    return saved


@router.get("/{system_id}/knowledge/search")
def search_knowledge_docs(
    system_id: int,
    q: str,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    system = require_system(session, system_id, org_id)
    return {"query": q, "context": get_relevant_context(q, str(system.id), max_chars=2000)}


# ---- 结构化记忆（AI 经验条目：人工确认入口） ----

from sqlmodel import select

from ..models.knowledge import (
    CONFIDENCE_HUMAN,
    VALIDITY_CONFIRMED,
    VALIDITY_FAILED,
    KnowledgeMemory,
)


@router.get("/{system_id}/knowledge/memories")
def list_knowledge_memories(
    system_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    system = require_system(session, system_id, org_id)
    rows = session.exec(
        select(KnowledgeMemory)
        .where(KnowledgeMemory.system_id == system.id)
        .order_by(KnowledgeMemory.updated_at.desc())
        .limit(50)
    ).all()
    return [
        {
            "id": m.id,
            "symptom": m.symptom,
            "root_cause": m.root_cause,
            "remedy": m.remedy,
            "source": m.source,
            "confidence": m.confidence,
            "validity": m.validity,
            "hit_count": m.hit_count,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in rows
    ]


@router.post("/knowledge/memories/{memory_id}/confirm")
def confirm_knowledge_memory(
    memory_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    """人工确认记忆有效：confidence → 1.0，检索权重提升到最高。"""
    from datetime import datetime, timezone

    from ..models.knowledge import CONFIDENCE_HUMAN, VALIDITY_CONFIRMED

    memory = session.get(KnowledgeMemory, memory_id)
    if not memory or memory.org_id != org_id:
        raise HTTPException(404, "记忆条目不存在")
    memory.validity = VALIDITY_CONFIRMED
    memory.confidence = CONFIDENCE_HUMAN
    memory.source = "human" if memory.source != "human" else memory.source
    memory.updated_at = datetime.now(timezone.utc)
    session.add(memory)
    session.commit()
    return {"id": memory.id, "validity": memory.validity, "confidence": memory.confidence}


@router.post("/knowledge/memories/{memory_id}/invalidate")
def invalidate_knowledge_memory(
    memory_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    """人工标记记忆失效：validity → failed（检索直接排除）。"""
    from datetime import datetime, timezone

    memory = session.get(KnowledgeMemory, memory_id)
    if not memory or memory.org_id != org_id:
        raise HTTPException(404, "记忆条目不存在")
    memory.validity = "failed"
    memory.confidence = 0.1
    memory.updated_at = datetime.now(timezone.utc)
    session.add(memory)
    session.commit()
    return {"id": memory.id, "validity": memory.validity, "confidence": memory.confidence}


@router.delete("/{system_id}/knowledge/docs/{name}")
def delete_knowledge_doc(
    system_id: int,
    name: str,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    _user=Depends(require_operator),
):
    system = require_system(session, system_id, org_id)
    path = _docs_dir(str(system.id)) / Path(name).name
    if path.exists():
        path.unlink()
    invalidate_index(str(system.id))
    return {"ok": True}

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

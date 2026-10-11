"""变更事件端点：登记与查询系统变更（发布/配置/重启等）。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..core.database import get_session
from ..core.deps import get_current_org_id, require_operator
from ..models.auth import User
from ..schemas.changes import ChangeCreate, ChangeOut
from ..services.changes import list_changes, record_change

router = APIRouter(prefix="/systems", tags=["changes"])


@router.post("/{system_id}/changes", response_model=ChangeOut, status_code=201)
def create_change(
    system_id: int,
    body: ChangeCreate,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(require_operator),
):
    try:
        event = record_change(
            session,
            org_id=org_id,
            system_id=system_id,
            title=body.title,
            service=body.service,
            kind=body.kind,
            description=body.description,
            actor=body.actor or (user.email if user else ""),
            external_id=body.external_id,
            occurred_at=body.occurred_at,
            details=body.details,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return ChangeOut(**event.model_dump())


@router.get("/{system_id}/changes", response_model=list[ChangeOut])
def get_changes(
    system_id: int,
    hours: int = Query(72, ge=1, le=24 * 30),
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    try:
        rows = list_changes(session, system_id, org_id, hours=hours, limit=limit)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return [ChangeOut(**row.model_dump()) for row in rows]

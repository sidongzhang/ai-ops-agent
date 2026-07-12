"""Audit log API."""
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.core.database import get_session
from app.core.deps import get_current_org_id
from app.schemas.audit import AuditLogPageOut
from app.services.audit import list_audit_logs

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=AuditLogPageOut)
def list_logs(
    system_id: int | None = None,
    event_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    return list_audit_logs(
        session,
        org_id,
        system_id=system_id,
        event_type=event_type,
        limit=limit,
        offset=offset,
    )

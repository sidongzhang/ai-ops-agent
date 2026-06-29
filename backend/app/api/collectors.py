"""采集器管理（用户侧，JWT）。为某套系统创建/列出采集器。"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from ..core.database import get_session
from ..core.deps import get_current_org_id
from ..schemas import CollectorCreate, CollectorCreated, CollectorOut
from ..services.collectors.service import (
    create_collector as create_collector_record,
    list_collectors as list_collector_records,
)

router = APIRouter(prefix="/systems/{system_id}/collectors", tags=["collectors"])


@router.post("", response_model=CollectorCreated, status_code=status.HTTP_201_CREATED)
def create_collector(system_id: int, body: CollectorCreate,
                     session: Session = Depends(get_session),
                     org_id: int = Depends(get_current_org_id)):
    try:
        return create_collector_record(session, system_id, org_id, body)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("", response_model=list[CollectorOut])
def list_collectors(system_id: int, session: Session = Depends(get_session),
                    org_id: int = Depends(get_current_org_id)):
    try:
        return list_collector_records(session, system_id, org_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))

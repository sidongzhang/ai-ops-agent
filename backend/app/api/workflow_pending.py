"""Org-wide workflow endpoints."""
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.core.database import get_session
from app.core.deps import get_current_org_id
from app.schemas.workflows import WorkflowPageOut
from app.services.workflows.service import get_action_catalog
from app.services.workflows.service import list_org_workflows as list_org_workflow_records
from app.services.workflows.service import list_pending_workflows

router = APIRouter(tags=["workflow"])


@router.get("/workflows/actions")
def list_actions():
    return get_action_catalog()


@router.get("/workflows", response_model=WorkflowPageOut)
def list_workflows(
    status: str | None = Query(default=None, pattern="^(pending|processed|approved|done|rejected|error)$"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    return list_org_workflow_records(session, org_id, status=status, limit=limit, offset=offset)


@router.get("/workflows/pending", response_model=WorkflowPageOut)
def list_pending(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    return list_pending_workflows(session, org_id, limit=limit, offset=offset)

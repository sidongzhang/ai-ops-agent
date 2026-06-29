"""Workflow approval API."""
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from ..core.database import get_session
from ..core.deps import get_current_org_id
from ..schemas import WorkflowDecision, WorkflowOut, WorkflowStart
from ..services.workflows.service import (
    create_workflow as create_workflow_record,
    decide_workflow as decide_workflow_record,
    get_workflow as get_workflow_record,
    list_workflows as list_workflow_records,
)

router = APIRouter(prefix="/systems", tags=["workflow"])


@router.post("/{system_id}/workflow", response_model=WorkflowOut, status_code=202)
async def create_workflow(
    system_id: int,
    body: WorkflowStart,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    try:
        return await create_workflow_record(session, system_id, org_id, body)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


@router.get("/{system_id}/workflow", response_model=list[WorkflowOut])
def list_workflows(
    system_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    try:
        return list_workflow_records(session, system_id, org_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/{system_id}/workflow/{wf_id}", response_model=WorkflowOut)
def get_workflow(
    system_id: int,
    wf_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    try:
        return get_workflow_record(session, system_id, wf_id, org_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{system_id}/workflow/{wf_id}/decision", response_model=WorkflowOut)
async def decide_workflow(
    system_id: int,
    wf_id: int,
    body: WorkflowDecision,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    try:
        return await decide_workflow_record(session, system_id, wf_id, org_id, body)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))

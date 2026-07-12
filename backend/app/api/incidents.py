"""Incident grouping API."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.core.database import get_session
from app.core.deps import get_current_org_id, get_current_user
from app.models.auth import User
from app.schemas.incidents import IncidentDetailOut, IncidentOut, IncidentSummaryOut
from app.schemas.messages import SystemMessageOut
from app.schemas.workflows import WorkflowOut
from app.services.incidents.service import (
    create_workflow_from_incident,
    get_incident,
    incident_summary,
    list_incident_messages,
    list_incident_outputs,
    to_incident_out,
)
from app.services.messages import ack_message, resolve_message

router = APIRouter(tags=["incidents"])


@router.get("/incidents/summary", response_model=IncidentSummaryOut)
def get_incident_summary(
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    return incident_summary(session, org_id)


@router.get("/incidents", response_model=list[IncidentOut])
def list_org_incidents(
    system_id: int | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    return list_incident_outputs(session, org_id, system_id=system_id, status=status, limit=limit)


@router.get("/incidents/{incident_id}", response_model=IncidentDetailOut)
def get_org_incident(
    incident_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    incident = get_incident(session, incident_id, org_id)
    if not incident:
        raise HTTPException(404, "事故不存在")
    messages = list_incident_messages(session, incident_id, org_id)
    return IncidentDetailOut(
        **to_incident_out(session, incident).model_dump(),
        messages=[SystemMessageOut(**msg.model_dump()) for msg in messages],
    )


@router.post("/incidents/{incident_id}/ack", response_model=IncidentOut)
def acknowledge_incident(
    incident_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(get_current_user),
):
    incident = get_incident(session, incident_id, org_id)
    if not incident:
        raise HTTPException(404, "事故不存在")
    messages = list_incident_messages(session, incident_id, org_id)
    for msg in messages:
        if msg.status not in ("acknowledged", "resolved"):
            ack_message(session, msg.id, org_id, actor_id=str(user.id))
    session.refresh(incident)
    from app.services.incidents.service import sync_incident_status
    sync_incident_status(session, incident_id)
    session.commit()
    session.refresh(incident)
    return to_incident_out(session, incident)


@router.post("/incidents/{incident_id}/resolve", response_model=IncidentOut)
def resolve_org_incident(
    incident_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(get_current_user),
):
    incident = get_incident(session, incident_id, org_id)
    if not incident:
        raise HTTPException(404, "事故不存在")
    messages = list_incident_messages(session, incident_id, org_id)
    for msg in messages:
        if msg.status != "resolved":
            resolve_message(session, msg.id, org_id, actor_id=str(user.id))
    session.refresh(incident)
    from app.services.incidents.service import sync_incident_status
    sync_incident_status(session, incident_id)
    session.commit()
    session.refresh(incident)
    return to_incident_out(session, incident)


@router.post("/incidents/{incident_id}/workflow", response_model=WorkflowOut, status_code=202)
async def create_incident_workflow(
    incident_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(get_current_user),
):
    try:
        return await create_workflow_from_incident(session, incident_id, org_id, user)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))

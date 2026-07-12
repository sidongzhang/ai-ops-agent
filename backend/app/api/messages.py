"""System message center API."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.core.database import get_session
from app.core.deps import get_current_org_id, get_current_user
from app.models.auth import User
from app.schemas.messages import SystemMessageOut, SystemMessagePageOut
from app.services.messages import (
    ack_message,
    count_system_messages,
    count_unread_messages,
    list_system_messages,
    mark_message_read,
    retry_failed_notifications,
    resolve_message,
)

router = APIRouter(tags=["messages"])


@router.get("/messages", response_model=SystemMessagePageOut)
def list_messages(
    status: str | None = None,
    message_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    items = list_system_messages(
        session,
        org_id,
        status=status,
        message_type=message_type,
        limit=limit,
        offset=offset,
    )
    total = count_system_messages(
        session,
        org_id,
        status=status,
        message_type=message_type,
    )
    return SystemMessagePageOut(
        items=[SystemMessageOut(**item.model_dump()) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/messages/unread-count", response_model=dict)
def unread_count(
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    return {"count": count_unread_messages(session, org_id)}


@router.get("/systems/{system_id}/messages", response_model=list[SystemMessageOut])
def list_messages_for_system(
    system_id: int,
    status: str | None = None,
    message_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    return list_system_messages(
        session,
        org_id,
        system_id=system_id,
        status=status,
        message_type=message_type,
        limit=limit,
    )


@router.post("/messages/{message_id}/read", response_model=SystemMessageOut)
def read_message(
    message_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(get_current_user),
):
    try:
        return mark_message_read(session, message_id, org_id, actor_id=str(user.id))
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post("/messages/{message_id}/ack", response_model=SystemMessageOut)
def acknowledge_message(
    message_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(get_current_user),
):
    try:
        return ack_message(session, message_id, org_id, actor_id=str(user.id))
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post("/messages/{message_id}/resolve", response_model=SystemMessageOut)
def resolve_system_message(
    message_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(get_current_user),
):
    try:
        return resolve_message(session, message_id, org_id, actor_id=str(user.id))
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post("/messages/{message_id}/retry-notifications", response_model=SystemMessageOut)
def retry_message_notifications(
    message_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(get_current_user),
):
    try:
        return retry_failed_notifications(
            session,
            message_id,
            org_id,
            actor_id=str(user.id),
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

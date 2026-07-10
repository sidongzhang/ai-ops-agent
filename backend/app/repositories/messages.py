"""Persistence helpers for the system message center."""
from sqlmodel import Session, func, select

from app.models.messages import SystemMessage


def list_messages_for_org(
    session: Session,
    org_id: int,
    *,
    system_id: int | None = None,
    status: str | None = None,
    message_type: str | None = None,
    limit: int = 50,
) -> list[SystemMessage]:
    query = select(SystemMessage).where(SystemMessage.org_id == org_id)
    if system_id is not None:
        query = query.where(SystemMessage.system_id == system_id)
    if status:
        query = query.where(SystemMessage.status == status)
    if message_type:
        query = query.where(SystemMessage.message_type == message_type)
    query = query.order_by(SystemMessage.created_at.desc()).limit(limit)
    return list(session.exec(query))


def get_message_for_org(session: Session, message_id: int, org_id: int) -> SystemMessage | None:
    return session.exec(
        select(SystemMessage).where(
            SystemMessage.id == message_id,
            SystemMessage.org_id == org_id,
        )
    ).first()


def count_unread_messages_for_org(session: Session, org_id: int) -> int:
    return session.exec(
        select(func.count(SystemMessage.id)).where(
            SystemMessage.org_id == org_id,
            SystemMessage.status == "unread",
        )
    ).one()


def find_message_by_request_id(session: Session, system_id: int, request_id: str) -> SystemMessage | None:
    if not request_id:
        return None
    messages = session.exec(
        select(SystemMessage).where(
            SystemMessage.system_id == system_id,
            SystemMessage.source == "openapi",
        )
    ).all()
    for message in messages:
        if (message.related or {}).get("request_id") == request_id:
            return message
    return None


def list_messages_for_system_public(
    session: Session,
    system_id: int,
    *,
    status: str | None = None,
    message_type: str | None = None,
    limit: int = 50,
) -> list[SystemMessage]:
    query = select(SystemMessage).where(SystemMessage.system_id == system_id)
    if status:
        query = query.where(SystemMessage.status == status)
    if message_type:
        query = query.where(SystemMessage.message_type == message_type)
    return list(session.exec(query.order_by(SystemMessage.created_at.desc()).limit(limit)))

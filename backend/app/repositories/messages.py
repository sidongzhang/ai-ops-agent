"""Persistence helpers for the system message center."""
from sqlmodel import Session, func, select

from app.models.messages import SystemMessage


def _message_query(
    session: Session,
    org_id: int,
    *,
    system_id: int | None = None,
    status: str | None = None,
    message_type: str | None = None,
):
    query = select(SystemMessage).where(SystemMessage.org_id == org_id)
    if system_id is not None:
        query = query.where(SystemMessage.system_id == system_id)
    if status:
        query = query.where(SystemMessage.status == status)
    if message_type:
        query = query.where(SystemMessage.message_type == message_type)
    return query


def count_messages_for_org(
    session: Session,
    org_id: int,
    *,
    system_id: int | None = None,
    status: str | None = None,
    message_type: str | None = None,
) -> int:
    query = _message_query(
        session,
        org_id,
        system_id=system_id,
        status=status,
        message_type=message_type,
    )
    return session.exec(select(func.count()).select_from(query.subquery())).one()


def list_messages_for_org(
    session: Session,
    org_id: int,
    *,
    system_id: int | None = None,
    status: str | None = None,
    message_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SystemMessage]:
    query = _message_query(
        session,
        org_id,
        system_id=system_id,
        status=status,
        message_type=message_type,
    )
    query = query.order_by(SystemMessage.created_at.desc()).offset(max(offset, 0)).limit(limit)
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
    # Public message listings also contain alerts generated internally by AIOps.
    # Those records have no related.request_id, so accept their numeric database
    # id only when the token's system_id owns the message.
    if request_id.isdigit():
        message = session.get(SystemMessage, int(request_id))
        if message and message.system_id == system_id:
            return message
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
    offset: int = 0,
) -> list[SystemMessage]:
    query = select(SystemMessage).where(SystemMessage.system_id == system_id)
    if status:
        query = query.where(SystemMessage.status == status)
    if message_type:
        query = query.where(SystemMessage.message_type == message_type)
    return list(session.exec(query.order_by(SystemMessage.created_at.desc()).offset(max(offset, 0)).limit(limit)))


def count_messages_for_system_public(
    session: Session,
    system_id: int,
    *,
    status: str | None = None,
    message_type: str | None = None,
) -> int:
    query = select(SystemMessage).where(SystemMessage.system_id == system_id)
    if status:
        query = query.where(SystemMessage.status == status)
    if message_type:
        query = query.where(SystemMessage.message_type == message_type)
    return session.exec(select(func.count()).select_from(query.subquery())).one()

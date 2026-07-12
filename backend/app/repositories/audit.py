"""Persistence helpers for audit logs."""
from sqlmodel import Session, func, select

from app.models.audit import AuditLog


def list_audit_logs_for_org(
    session: Session,
    org_id: int,
    *,
    system_id: int | None = None,
    event_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditLog]:
    query = _audit_query(session, org_id, system_id=system_id, event_type=event_type)
    return list(session.exec(query.order_by(AuditLog.created_at.desc()).offset(max(offset, 0)).limit(limit)))


def count_audit_logs_for_org(
    session: Session,
    org_id: int,
    *,
    system_id: int | None = None,
    event_type: str | None = None,
) -> int:
    query = _audit_query(session, org_id, system_id=system_id, event_type=event_type)
    return session.exec(select(func.count()).select_from(query.subquery())).one()


def _audit_query(
    session: Session,
    org_id: int,
    *,
    system_id: int | None = None,
    event_type: str | None = None,
):
    query = select(AuditLog).where(AuditLog.org_id == org_id)
    if system_id is not None:
        query = query.where(AuditLog.system_id == system_id)
    if event_type:
        query = query.where(AuditLog.event_type == event_type)
    return query

"""Audit logging service."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete as sa_delete
from sqlmodel import Session

from app.models.audit import AuditLog
from app.repositories.audit import count_audit_logs_for_org, list_audit_logs_for_org
from app.schemas.audit import AuditLogOut, AuditLogPageOut


def record_audit_event(
    session: Session,
    *,
    org_id: int,
    event_type: str,
    system_id: int | None = None,
    actor_type: str = "platform",
    actor_id: str = "",
    target_type: str = "",
    target_id: str = "",
    status: str = "success",
    input: dict | None = None,
    output: dict | None = None,
    commit: bool = False,
) -> AuditLog:
    log = AuditLog(
        org_id=org_id,
        system_id=system_id,
        actor_type=actor_type,
        actor_id=str(actor_id or ""),
        event_type=event_type,
        target_type=target_type,
        target_id=str(target_id or ""),
        status=status,
        input=input or {},
        output=output or {},
    )
    session.add(log)
    if commit:
        session.commit()
        session.refresh(log)
    else:
        session.flush()
    return log


def list_audit_logs(
    session: Session,
    org_id: int,
    *,
    system_id: int | None = None,
    event_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> AuditLogPageOut:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    rows = list_audit_logs_for_org(
        session,
        org_id,
        system_id=system_id,
        event_type=event_type,
        limit=limit,
        offset=offset,
    )
    total = count_audit_logs_for_org(
        session,
        org_id,
        system_id=system_id,
        event_type=event_type,
    )
    return AuditLogPageOut(
        items=[AuditLogOut(**log.model_dump()) for log in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


def purge_old_audit_logs(session: Session, retention_days: int) -> int:
    """删除超过保留期的审计日志，返回删除条数。retention_days <= 0 表示关闭。"""
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = session.execute(sa_delete(AuditLog).where(AuditLog.created_at < cutoff))
    session.commit()
    return int(result.rowcount or 0)

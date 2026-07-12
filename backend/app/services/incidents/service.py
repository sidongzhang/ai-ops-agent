"""Group related alerts into incidents."""
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.models.incidents import Incident
from app.models.messages import SystemMessage
from app.models.auth import User
from app.models.systems import MonitoredSystem
from app.schemas.incidents import IncidentOut, IncidentSummaryOut
from app.schemas.workflows import WorkflowOut, WorkflowStart
from app.services.workflows.service import create_workflow

INCIDENT_WINDOW_HOURS = 2


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_status(message_status: str) -> str:
    if message_status == "resolved":
        return "resolved"
    if message_status == "acknowledged":
        return "acknowledged"
    return "open"


def find_open_incident(
    session: Session,
    system: MonitoredSystem,
    failed_services: list[str],
    *,
    as_of: datetime | None = None,
) -> Incident | None:
    now = as_of or utcnow()
    cutoff = now - timedelta(hours=INCIDENT_WINDOW_HOURS)
    failed_set = set(failed_services)
    incidents = session.exec(
        select(Incident).where(
            Incident.org_id == system.org_id,
            Incident.system_id == system.id,
            Incident.status != "resolved",
            Incident.last_seen >= cutoff,
        ).order_by(Incident.last_seen.desc())
    ).all()
    for incident in incidents:
        existing = set(incident.failed_services or [])
        if existing & failed_set:
            return incident
    return None


def attach_message_to_incident(
    session: Session,
    system: MonitoredSystem,
    message: SystemMessage,
    failed_services: list[str],
    *,
    as_of: datetime | None = None,
) -> Incident:
    now = as_of or utcnow()
    incident = find_open_incident(session, system, failed_services, as_of=now)
    if incident:
        merged = sorted(set(incident.failed_services or []) | set(failed_services))
        incident.failed_services = merged
        incident.message_count += 1
        incident.last_seen = now
    else:
        services = "、".join(failed_services)
        incident = Incident(
            org_id=system.org_id,
            system_id=system.id,
            title=f"系统「{system.name}」服务异常",
            status="open",
            severity=message.severity or "warning",
            failed_services=sorted(set(failed_services)),
            message_count=1,
            first_seen=now,
            last_seen=now,
        )
        session.add(incident)
        session.flush()

    message.incident_id = incident.id
    related = dict(message.related or {})
    related["incident_id"] = incident.id
    related["failed_services"] = incident.failed_services
    message.related = related
    session.add(message)
    session.add(incident)
    return incident


def sync_incident_status(session: Session, incident_id: int) -> None:
    incident = session.get(Incident, incident_id)
    if not incident:
        return
    messages = session.exec(
        select(SystemMessage).where(SystemMessage.incident_id == incident_id)
    ).all()
    if not messages:
        return

    statuses = [msg.status for msg in messages]
    if all(status == "resolved" for status in statuses):
        incident.status = "resolved"
        incident.resolved_at = utcnow()
    elif any(status == "acknowledged" for status in statuses):
        incident.status = "acknowledged"
    else:
        incident.status = "open"

    all_failed: set[str] = set()
    for msg in messages:
        all_failed.update((msg.related or {}).get("failed_services") or [])
    if all_failed:
        incident.failed_services = sorted(all_failed)
    incident.message_count = len(messages)
    incident.last_seen = max(msg.created_at for msg in messages)
    session.add(incident)


def list_incidents(
    session: Session,
    org_id: int,
    *,
    system_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[Incident]:
    limit = max(1, min(limit, 100))
    query = select(Incident).where(Incident.org_id == org_id)
    if system_id is not None:
        query = query.where(Incident.system_id == system_id)
    if status:
        query = query.where(Incident.status == status)
    query = query.order_by(Incident.last_seen.desc()).limit(limit)
    return list(session.exec(query).all())


def get_incident(session: Session, incident_id: int, org_id: int) -> Incident | None:
    incident = session.get(Incident, incident_id)
    if not incident or incident.org_id != org_id:
        return None
    return incident


def list_incident_messages(session: Session, incident_id: int, org_id: int) -> list[SystemMessage]:
    incident = get_incident(session, incident_id, org_id)
    if not incident:
        return []
    return list(session.exec(
        select(SystemMessage)
        .where(SystemMessage.incident_id == incident_id, SystemMessage.org_id == org_id)
        .order_by(SystemMessage.created_at.desc())
    ).all())


def build_incident_workflow_question(incident: Incident, messages: list[SystemMessage]) -> tuple[str, dict]:
    failed_services = incident.failed_services or []
    message_summaries = [
        {
            "title": str(message.title or "")[:120],
            "summary": str(message.summary or message.content or "")[:300],
            "severity": message.severity,
            "status": message.status,
        }
        for message in messages[:5]
    ]
    services_text = "、".join(failed_services) if failed_services else "未明确"
    question = (
        f"事故「{incident.title}」需要处理。"
        f"影响服务：{services_text}。"
        "请基于已注册服务、健康检查、日志和当前异常信息给出一个最合适的处置动作；"
        "如果可以自动修复，请生成需要审批的修复提案；如果信息不足，请先建议拉取日志或健康检查。"
    )
    return question, {
        "source": "incident",
        "incident_id": incident.id,
        "severity": incident.severity,
        "failed_services": failed_services,
        "message_count": incident.message_count,
        "messages": message_summaries,
    }


async def create_workflow_from_incident(
    session: Session,
    incident_id: int,
    org_id: int,
    current_user: User,
) -> WorkflowOut:
    incident = get_incident(session, incident_id, org_id)
    if not incident:
        raise LookupError("事故不存在")
    if incident.status == "resolved":
        raise ValueError("事故已解决，无需创建修复提案")
    messages = list_incident_messages(session, incident_id, org_id)
    question, context = build_incident_workflow_question(incident, messages)
    return await create_workflow(
        session,
        incident.system_id,
        org_id,
        WorkflowStart(question=question, context=context),
        current_user,
    )


def _system_names(session: Session, system_ids: set[int]) -> dict[int, str]:
    if not system_ids:
        return {}
    rows = session.exec(select(MonitoredSystem).where(MonitoredSystem.id.in_(system_ids))).all()
    return {row.id: row.name for row in rows}


def to_incident_out(session: Session, incident: Incident) -> IncidentOut:
    names = _system_names(session, {incident.system_id})
    return IncidentOut(
        **incident.model_dump(),
        system_name=names.get(incident.system_id, f"系统 #{incident.system_id}"),
    )


def list_incident_outputs(
    session: Session,
    org_id: int,
    *,
    system_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[IncidentOut]:
    incidents = list_incidents(session, org_id, system_id=system_id, status=status, limit=limit)
    names = _system_names(session, {item.system_id for item in incidents})
    return [
        IncidentOut(
            **item.model_dump(),
            system_name=names.get(item.system_id, f"系统 #{item.system_id}"),
        )
        for item in incidents
    ]


def incident_summary(session: Session, org_id: int) -> IncidentSummaryOut:
    rows = session.exec(select(Incident).where(Incident.org_id == org_id)).all()
    summary = IncidentSummaryOut(total=len(rows))
    for row in rows:
        if row.status == "open":
            summary.open += 1
        elif row.status == "acknowledged":
            summary.acknowledged += 1
        elif row.status == "resolved":
            summary.resolved += 1
    return summary


def _failed_services_from_message(message: SystemMessage) -> list[str]:
    related = (message.related or {}).get("failed_services") or []
    if related:
        return [str(item).strip() for item in related if str(item).strip()]
    for text in (message.summary, message.content):
        if not text or "异常服务" not in text:
            continue
        part = text.split("异常服务", 1)[1].lstrip("：:").split("。")[0]
        services = [item.strip() for item in part.replace("、", ",").split(",") if item.strip()]
        if services:
            return services
    return []


def backfill_incidents(session: Session, *, org_id: int | None = None) -> int:
    """Link legacy alert messages without incident_id into incidents."""
    query = (
        select(SystemMessage)
        .where(
            SystemMessage.message_type == "alert",
            SystemMessage.incident_id.is_(None),
        )
        .order_by(SystemMessage.created_at.asc())
    )
    if org_id is not None:
        query = query.where(SystemMessage.org_id == org_id)

    messages = list(session.exec(query).all())
    if not messages:
        return 0

    systems: dict[int, MonitoredSystem | None] = {}
    linked = 0
    touched_incidents: set[int] = set()
    for message in messages:
        failed_services = _failed_services_from_message(message)
        if not failed_services:
            continue
        if message.system_id not in systems:
            systems[message.system_id] = session.get(MonitoredSystem, message.system_id)
        system = systems[message.system_id]
        if not system:
            continue
        as_of = message.created_at or utcnow()
        incident = attach_message_to_incident(
            session,
            system,
            message,
            failed_services,
            as_of=as_of,
        )
        touched_incidents.add(incident.id)
        linked += 1

    for incident_id in touched_incidents:
        sync_incident_status(session, incident_id)

    if linked:
        session.commit()
    return linked

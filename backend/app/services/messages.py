"""Business logic for user-visible system messages."""
from datetime import datetime, timezone

from sqlmodel import Session

from app.models.messages import SystemMessage
from app.models.systems import MonitoredSystem
from app.repositories.messages import count_unread_messages_for_org, get_message_for_org, list_messages_for_org
from app.services.audit import record_audit_event


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_alert_suggestion(failed_services: list[str]) -> list[str]:
    services = "、".join(failed_services)
    return [
        f"先在系统详情页重新探活，确认 {services} 是否仍然异常。",
        "查看监控页最近 CPU、内存、连接数、积压等指标是否同步异常。",
        "进入 AI 诊断页追问异常服务，获取更完整的日志和指标分析。",
    ]


def create_alert_message(
    session: Session,
    system: MonitoredSystem,
    failed_services: list[str],
    *,
    related: dict | None = None,
) -> SystemMessage:
    services = "、".join(failed_services)
    message = SystemMessage(
        org_id=system.org_id,
        system_id=system.id,
        message_type="alert",
        severity="warning",
        title=f"系统「{system.name}」存在异常服务",
        summary=f"异常服务：{services}",
        content=f"平台巡检发现系统「{system.name}」以下服务从正常变为异常：{services}。",
        diagnosis="已发现服务健康状态异常，建议优先确认服务连通性、资源指标和近期日志。",
        suggestion=build_alert_suggestion(failed_services),
        source="scheduled_health_check",
        related=related or {"failed_services": failed_services},
    )
    session.add(message)
    session.flush()
    return message


def list_system_messages(
    session: Session,
    org_id: int,
    *,
    system_id: int | None = None,
    status: str | None = None,
    message_type: str | None = None,
    limit: int = 50,
) -> list[SystemMessage]:
    limit = max(1, min(limit, 200))
    return list_messages_for_org(
        session,
        org_id,
        system_id=system_id,
        status=status,
        message_type=message_type,
        limit=limit,
    )


def count_unread_messages(session: Session, org_id: int) -> int:
    return count_unread_messages_for_org(session, org_id)


def mark_message_read(
    session: Session,
    message_id: int,
    org_id: int,
    *,
    actor_type: str = "user",
    actor_id: str = "",
) -> SystemMessage:
    message = _require_message(session, message_id, org_id)
    if message.status == "unread":
        message.status = "read"
    if not message.read_at:
        message.read_at = utcnow()
    session.add(message)
    session.commit()
    session.refresh(message)
    record_audit_event(
        session,
        org_id=org_id,
        system_id=message.system_id,
        actor_type=actor_type,
        actor_id=actor_id,
        event_type="message.read",
        target_type="message",
        target_id=message.id,
        output={"status": message.status},
        commit=True,
    )
    return message


def ack_message(
    session: Session,
    message_id: int,
    org_id: int,
    *,
    actor_type: str = "user",
    actor_id: str = "",
) -> SystemMessage:
    message = _require_message(session, message_id, org_id)
    message.status = "acknowledged"
    if not message.read_at:
        message.read_at = utcnow()
    message.ack_at = utcnow()
    session.add(message)
    session.commit()
    session.refresh(message)
    record_audit_event(
        session,
        org_id=org_id,
        system_id=message.system_id,
        actor_type=actor_type,
        actor_id=actor_id,
        event_type="message.acknowledged",
        target_type="message",
        target_id=message.id,
        output={"status": message.status},
        commit=True,
    )
    return message


def resolve_message(
    session: Session,
    message_id: int,
    org_id: int,
    *,
    actor_type: str = "user",
    actor_id: str = "",
) -> SystemMessage:
    message = _require_message(session, message_id, org_id)
    message.status = "resolved"
    if not message.read_at:
        message.read_at = utcnow()
    if not message.ack_at:
        message.ack_at = utcnow()
    message.resolved_at = utcnow()
    session.add(message)
    session.commit()
    session.refresh(message)
    record_audit_event(
        session,
        org_id=org_id,
        system_id=message.system_id,
        actor_type=actor_type,
        actor_id=actor_id,
        event_type="message.resolved",
        target_type="message",
        target_id=message.id,
        output={"status": message.status},
        commit=True,
    )
    return message


def retry_failed_notifications(
    session: Session,
    message_id: int,
    org_id: int,
    *,
    actor_id: str = "",
) -> SystemMessage:
    """Retry only failed external channels for an existing alert message."""
    message = _require_message(session, message_id, org_id)
    failed_results = [
        result
        for result in (message.channels or [])
        if result.get("type") != "web" and result.get("status") == "failed"
    ]
    if not failed_results:
        raise ValueError("这条消息没有需要重试的失败通知")

    failed_services = (message.related or {}).get("failed_services") or []
    if not failed_services:
        raise ValueError("这条消息缺少异常服务信息，无法重新生成通知")

    from app.services.notifications.alerts import send_alert_channel_with_retry
    from app.services.systems.service import require_system

    system = require_system(session, message.system_id, org_id)
    retried_by_type = {}
    for previous in failed_results:
        result = send_alert_channel_with_retry(
            previous.get("type", ""),
            system.notify or {},
            system.name,
            failed_services,
        )
        result["attempts"] = int(previous.get("attempts") or 0) + int(result.get("attempts") or 0)
        retried_by_type[result["type"]] = result

    message.channels = [
        retried_by_type.get(result.get("type"), result)
        for result in (message.channels or [])
    ]
    session.add(message)
    still_failed = [
        result["type"]
        for result in retried_by_type.values()
        if result.get("status") == "failed"
    ]
    record_audit_event(
        session,
        org_id=org_id,
        system_id=message.system_id,
        actor_type="user",
        actor_id=actor_id,
        event_type="notification.retried",
        target_type="message",
        target_id=message.id,
        status="failed" if still_failed else "success",
        input={"channels": list(retried_by_type)},
        output={
            "channel_results": list(retried_by_type.values()),
            "still_failed": still_failed,
        },
    )
    session.commit()
    session.refresh(message)
    return message


def _require_message(session: Session, message_id: int, org_id: int) -> SystemMessage:
    message = get_message_for_org(session, message_id, org_id)
    if not message:
        raise LookupError("消息不存在")
    return message

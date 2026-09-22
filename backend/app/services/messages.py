"""Business logic for user-visible system messages."""
from datetime import datetime, timezone

from sqlmodel import Session

from app.models.messages import SystemMessage
from app.models.systems import MonitoredSystem
from app.repositories.messages import (
    count_messages_for_org,
    count_unread_messages_for_org,
    get_message_for_org,
    list_messages_for_org,
)
from app.services.audit import record_audit_event


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_alert_suggestion(failed_services: list[str]) -> list[str]:
    suggestions = []
    for service in failed_services:
        name = str(service)
        lowered = name.lower()
        if "redis" in lowered:
            suggestions.append(f"{name}：先检查内存占用、连接数和慢查询；确认无误后再考虑重启。")
        elif "mysql" in lowered or "数据库" in name:
            suggestions.append(f"{name}：检查数据库连接数、磁盘空间和 mysqld 日志，优先确认是否为连接耗尽。")
        elif "kafka" in lowered or "消息" in name:
            suggestions.append(f"{name}：检查 Broker 状态、消费者组积压和磁盘空间，先定位积压来源再处理。")
        elif any(marker in lowered for marker in ("api", "spring", "http", "nginx", "web")):
            suggestions.append(f"{name}：先查看最近 ERROR/Exception 日志并重新探活，确认依赖服务和健康端点。")
        else:
            suggestions.append(f"{name}：重新探活并查看最近日志，确认网络、进程和依赖服务状态。")

    services = "、".join(failed_services)
    suggestions.extend([
        f"先在系统详情页重新探活，确认 {services} 是否仍然异常。",
        "如果服务已配置授权重启目标，可在 AI 诊断提出方案后审批执行；不要直接重复重启。",
        "进入 AI 诊断页追问异常服务，获取日志、指标和具体恢复步骤。",
    ])
    return suggestions


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
    from app.services.incidents.service import attach_message_to_incident
    attach_message_to_incident(session, system, message, failed_services)
    return message


def list_system_messages(
    session: Session,
    org_id: int,
    *,
    system_id: int | None = None,
    status: str | None = None,
    message_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SystemMessage]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    return list_messages_for_org(
        session,
        org_id,
        system_id=system_id,
        status=status,
        message_type=message_type,
        limit=limit,
        offset=offset,
    )


def count_system_messages(
    session: Session,
    org_id: int,
    *,
    system_id: int | None = None,
    status: str | None = None,
    message_type: str | None = None,
) -> int:
    return count_messages_for_org(
        session,
        org_id,
        system_id=system_id,
        status=status,
        message_type=message_type,
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
    if message.incident_id:
        from app.services.incidents.service import sync_incident_status
        sync_incident_status(session, message.incident_id)
        session.commit()
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
    if message.incident_id:
        from app.services.incidents.service import sync_incident_status
        sync_incident_status(session, message.incident_id)
        session.commit()
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
    from app.services.notifications.alerts import build_alert_delivery_snapshot
    from app.services.notifications.deliveries import record_notification_delivery
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
        snapshot = build_alert_delivery_snapshot(
            result["type"],
            system.notify or {},
            system.name,
            failed_services,
        )
        record_notification_delivery(
            session,
            message,
            channel=result["type"],
            recipient=snapshot.get("recipient", ""),
            subject=snapshot.get("subject", message.title),
            body=snapshot.get("body", message.content),
            payload={**snapshot.get("payload", {}), "retry": True},
            result=result,
        )

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


def retry_message_processing(
    session: Session,
    message_id: int,
    org_id: int,
    *,
    actor_id: str = "",
) -> SystemMessage:
    message = _require_message(session, message_id, org_id)
    related = dict(message.related or {})
    processing_status = related.get("processing_status")
    if processing_status not in {"failed", "enqueue_failed"}:
        raise ValueError("这条消息没有失败的后台分析任务")
    if message.message_type == "log_analysis":
        raise ValueError("日志原文不会保存在平台，请让对方系统使用同一个 request_id 重新上传日志")

    from app.services.message_processing import enqueue_message_processing, mark_message_processing_queued

    message.diagnosis = "后台分析已重新排队，请稍后查看结果。"
    message.suggestion = ["稍后刷新消息中心查看分析结果。", "如仍失败，请查看审计日志中的失败原因。"]
    session.add(message)
    session.commit()
    session.refresh(message)
    message = mark_message_processing_queued(session, message)
    try:
        enqueue_message_processing(message.id)
        event_status = "success"
        output = {"message_id": message.id, "processing_status": (message.related or {}).get("processing_status")}
    except Exception as exc:
        related = dict(message.related or {})
        related["processing_status"] = "enqueue_failed"
        related["processing_error"] = str(exc)
        message.related = related
        session.add(message)
        session.commit()
        session.refresh(message)
        event_status = "failed"
        output = {"error": str(exc)}

    record_audit_event(
        session,
        org_id=org_id,
        system_id=message.system_id,
        actor_type="user",
        actor_id=actor_id,
        event_type="message.processing_retried",
        target_type="message",
        target_id=message.id,
        status=event_status,
        input={"previous_status": processing_status, "message_type": message.message_type},
        output=output,
        commit=True,
    )
    return message


def _require_message(session: Session, message_id: int, org_id: int) -> SystemMessage:
    message = get_message_for_org(session, message_id, org_id)
    if not message:
        raise LookupError("消息不存在")
    return message

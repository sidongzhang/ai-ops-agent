"""OpenAPI ingestion services for external business systems."""
from datetime import datetime, timezone

from sqlmodel import Session

from app.models.messages import SystemMessage
from app.models.systems import MonitoredSystem
from app.models.tokens import SystemToken
from app.repositories.messages import (
    count_messages_for_system_public,
    find_message_by_request_id,
    list_messages_for_system_public,
)
from app.schemas.messages import SystemMessageOut
import json

from app.schemas.openapi import OpenAlertIn, OpenHealthIn, OpenMessageIn, OpenMessagePageOut, OpenProductionReportIn
from app.services.audit import record_audit_event
from app.services.messages import ack_message, mark_message_read, resolve_message
from app.services.message_processing import (
    enqueue_message_processing,
    enrich_message,
    mark_message_processing_enqueue_failed,
    mark_message_processing_queued,
)


def _processing_text(kind: str, need_llm_process: bool) -> tuple[str, list[str]]:
    if need_llm_process:
        return (
            f"业务系统主动提交{kind}，平台已记录，并标记为需要智能分析。当前先进入消息中心，后续分析结果会回写到同一条消息。",
            [
                "先在消息中心确认该消息，避免重复跟进。",
                "如问题紧急，可直接进入系统详情页发起 AI 诊断。",
                "后续智能分析完成后，应继续在同一条消息中查看结论和建议。",
            ],
        )
    return (
        f"业务系统主动提交{kind}，平台已统一存储，可在消息中心查询。",
        [
            "如需跟进，请在消息中心确认阅读状态。",
            "如报告数据异常，可进入系统详情页结合监控和 AI 诊断继续分析。",
        ],
    )


def submit_open_alert(
    session: Session,
    system: MonitoredSystem,
    token: SystemToken,
    body: OpenAlertIn,
    *,
    enqueue_processing: bool = True,
) -> SystemMessage:
    existing = find_message_by_request_id(session, system.id, body.request_id)
    if existing:
        record_audit_event(
            session,
            org_id=system.org_id,
            system_id=system.id,
            actor_type="system_token",
            actor_id=token.id,
            event_type="openapi.alert.duplicate",
            target_type="message",
            target_id=existing.id,
            input={"request_id": body.request_id},
            output={"message_id": existing.id},
            commit=True,
        )
        session.refresh(existing)
        return existing

    diagnosis, suggestion = _processing_text("告警", body.need_llm_process)
    if not body.need_llm_process:
        diagnosis = "业务系统主动上报告警，平台已记录。可在系统详情页结合监控和 AI 诊断继续分析。"
        suggestion = [
            "确认业务系统上报的异常是否仍在持续。",
            "查看该系统最近健康检查、指标和日志，判断是否需要人工处理。",
            "如影响业务，请在消息中心确认并跟进解决状态。",
        ]

    message = SystemMessage(
        org_id=system.org_id,
        system_id=system.id,
        message_type="alert",
        severity=body.severity,
        title=body.title,
        summary=body.summary or body.content[:160],
        content=body.content,
        diagnosis=diagnosis,
        suggestion=suggestion,
        source="openapi",
        related={
            "request_id": body.request_id,
            "token_id": token.id,
            "context": body.context,
            "need_llm_process": body.need_llm_process,
        },
    )
    token.last_used_at = datetime.now(timezone.utc)
    session.add(message)
    session.add(token)
    session.commit()
    session.refresh(message)
    record_audit_event(
        session,
        org_id=system.org_id,
        system_id=system.id,
        actor_type="system_token",
        actor_id=token.id,
        event_type="openapi.alert.created",
        target_type="message",
        target_id=message.id,
        input={"request_id": body.request_id, "severity": body.severity},
        output={"message_id": message.id},
        commit=True,
    )
    session.refresh(message)
    if body.need_llm_process:
        if enqueue_processing:
            message.related = {**(message.related or {}), "notify_after_processing": True}
            session.add(message)
            session.commit()
            session.refresh(message)
            message = mark_message_processing_queued(session, message)
            try:
                enqueue_message_processing(message.id)
            except Exception as exc:
                message = mark_message_processing_enqueue_failed(session, message, str(exc))
            return message
        return enrich_message(session, message)
    from app.services.notifications.alerts import deliver_message_notifications

    return deliver_message_notifications(session, system, message, actor_id=str(token.id))


def submit_open_message(
    session: Session,
    system: MonitoredSystem,
    token: SystemToken,
    body: OpenMessageIn,
    *,
    message_type: str = "message",
    enqueue_processing: bool = True,
    related_extra: dict | None = None,
) -> SystemMessage:
    existing = find_message_by_request_id(session, system.id, body.request_id)
    if existing:
        record_audit_event(
            session,
            org_id=system.org_id,
            system_id=system.id,
            actor_type="system_token",
            actor_id=token.id,
            event_type=f"openapi.{message_type}.duplicate",
            target_type="message",
            target_id=existing.id,
            input={"request_id": body.request_id},
            output={"message_id": existing.id},
            commit=True,
        )
        session.refresh(existing)
        return existing

    label = {
        "message": "业务消息",
        "daily_report": "日报",
        "weekly_report": "周报",
        "monthly_report": "月报",
    }.get(message_type, message_type)
    diagnosis, suggestion = _processing_text(label, body.need_llm_process)
    message = SystemMessage(
        org_id=system.org_id,
        system_id=system.id,
        message_type=message_type,
        severity=body.severity,
        title=body.title,
        summary=body.summary or body.content[:160],
        content=body.content,
        diagnosis=diagnosis,
        suggestion=suggestion,
        source="openapi",
        related={
            "request_id": body.request_id,
            "token_id": token.id,
            "context": body.context,
            "need_llm_process": body.need_llm_process,
            **(related_extra or {}),
        },
    )
    token.last_used_at = datetime.now(timezone.utc)
    session.add(message)
    session.add(token)
    session.commit()
    session.refresh(message)
    record_audit_event(
        session,
        org_id=system.org_id,
        system_id=system.id,
        actor_type="system_token",
        actor_id=token.id,
        event_type=f"openapi.{message_type}.created",
        target_type="message",
        target_id=message.id,
        input={"request_id": body.request_id, "severity": body.severity},
        output={"message_id": message.id},
        commit=True,
    )
    session.refresh(message)
    if body.need_llm_process:
        if enqueue_processing:
            message = mark_message_processing_queued(session, message)
            try:
                enqueue_message_processing(message.id)
            except Exception as exc:
                message = mark_message_processing_enqueue_failed(session, message, str(exc))
            return message
        return enrich_message(session, message)
    return message


def submit_open_production_report(
    session: Session,
    system: MonitoredSystem,
    token: SystemToken,
    body: OpenProductionReportIn,
    *,
    message_type: str,
) -> SystemMessage:
    report_name = {
        "daily_report": "生产日报",
        "weekly_report": "生产周报",
        "monthly_report": "生产月报",
    }.get(message_type, "生产报告")
    title = body.title.strip() or f"{system.name}{report_name}"
    content = json.dumps(
        {
            "period": body.period,
            "summary": body.summary,
            "data": body.data,
            "context": body.context,
        },
        ensure_ascii=False,
        indent=2,
    )
    return submit_open_message(
        session,
        system,
        token,
        OpenMessageIn(
            request_id=body.request_id,
            title=title,
            summary=body.summary or f"外部系统提交{report_name}，等待平台加工。",
            content=content,
            severity="info",
            need_llm_process=True,
            context={**body.context, "period": body.period},
        ),
        message_type=message_type,
        related_extra={
            "processing_kind": "production_report",
            "notify_after_processing": True,
            "period": body.period,
        },
    )


def update_open_message_status(
    session: Session,
    system: MonitoredSystem,
    token: SystemToken,
    request_id: str,
    status: str,
) -> SystemMessage:
    message = get_open_message_by_request_id(session, system, request_id)
    if status == "read":
        result = mark_message_read(session, message.id, system.org_id, actor_type="system_token", actor_id=str(token.id))
    elif status == "acknowledged":
        result = ack_message(session, message.id, system.org_id, actor_type="system_token", actor_id=str(token.id))
    elif status == "resolved":
        result = resolve_message(session, message.id, system.org_id, actor_type="system_token", actor_id=str(token.id))
    else:
        raise ValueError("status 仅支持 read / acknowledged / resolved")
    token.last_used_at = datetime.now(timezone.utc)
    session.add(token)
    session.commit()
    session.refresh(result)
    return result


def submit_open_health(
    session: Session,
    system: MonitoredSystem,
    token: SystemToken,
    body: OpenHealthIn,
) -> dict:
    now = datetime.now(timezone.utc)
    system.last_health = {"services": [service.model_dump() for service in body.services]}
    system.last_report_at = now
    token.last_used_at = now
    session.add(system)
    session.add(token)
    session.commit()
    record_audit_event(
        session,
        org_id=system.org_id,
        system_id=system.id,
        actor_type="system_token",
        actor_id=token.id,
        event_type="openapi.health.pushed",
        target_type="system",
        target_id=system.id,
        input={"request_id": body.request_id, "services": len(body.services)},
        output={"healthy": all(service.ok for service in body.services) if body.services else True},
        commit=True,
    )
    return {
        "ok": True,
        "system_id": system.id,
        "healthy": all(service.ok for service in body.services) if body.services else True,
        "received": len(body.services),
        "reported_at": now.isoformat(),
    }


def list_open_messages(
    session: Session,
    system: MonitoredSystem,
    *,
    status: str | None = None,
    message_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> OpenMessagePageOut:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    items = [
        SystemMessageOut(**message.model_dump())
        for message in list_messages_for_system_public(
            session,
            system.id,
            status=status,
            message_type=message_type,
            limit=limit,
            offset=offset,
        )
    ]
    total = count_messages_for_system_public(
        session,
        system.id,
        status=status,
        message_type=message_type,
    )
    return OpenMessagePageOut(items=items, total=total, offset=offset, limit=limit)


def get_open_message_by_request_id(
    session: Session,
    system: MonitoredSystem,
    request_id: str,
) -> SystemMessage:
    message = find_message_by_request_id(session, system.id, request_id)
    if not message:
        raise LookupError("消息不存在")
    return message

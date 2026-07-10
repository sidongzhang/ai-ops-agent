"""OpenAPI ingestion services for external business systems."""
from datetime import datetime, timezone

from sqlmodel import Session

from app.models.messages import SystemMessage
from app.models.systems import MonitoredSystem
from app.models.tokens import SystemToken
from app.repositories.messages import find_message_by_request_id, list_messages_for_system_public
from app.schemas.messages import SystemMessageOut
from app.schemas.openapi import OpenAlertIn, OpenHealthIn, OpenMessageIn
from app.services.audit import record_audit_event
from app.services.message_processing import enrich_message


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
        return enrich_message(session, message)
    return message


def submit_open_message(
    session: Session,
    system: MonitoredSystem,
    token: SystemToken,
    body: OpenMessageIn,
    *,
    message_type: str = "message",
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
        return enrich_message(session, message)
    return message


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
) -> list[SystemMessageOut]:
    limit = max(1, min(limit, 100))
    return [
        SystemMessageOut(**message.model_dump())
        for message in list_messages_for_system_public(
            session,
            system.id,
            status=status,
            message_type=message_type,
            limit=limit,
        )
    ]


def get_open_message_by_request_id(
    session: Session,
    system: MonitoredSystem,
    request_id: str,
) -> SystemMessage:
    message = find_message_by_request_id(session, system.id, request_id)
    if not message:
        raise LookupError("消息不存在")
    return message

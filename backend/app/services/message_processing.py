"""Lightweight message enrichment for OpenAPI submissions."""
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.core.database import engine
from app.models.messages import SystemMessage
from app.services.audit import record_audit_event


def mark_message_processing_queued(session: Session, message: SystemMessage) -> SystemMessage:
    related = dict(message.related or {})
    related["need_llm_process"] = True
    related["processing_status"] = "queued"
    related["queued_at"] = datetime.now(timezone.utc).isoformat()
    # 重新排队时必须清掉上一次的失败痕迹，否则界面会一直显示旧的错误信息。
    related.pop("processing_error", None)
    related.pop("processing_failed_at", None)
    message.related = related
    session.add(message)
    session.commit()
    session.refresh(message)
    return message


def mark_message_processing_enqueue_failed(session: Session, message: SystemMessage, error: str) -> SystemMessage:
    related = dict(message.related or {})
    related["need_llm_process"] = True
    related["processing_status"] = "enqueue_failed"
    related["processing_error"] = error
    related["processing_failed_at"] = datetime.now(timezone.utc).isoformat()
    message.related = related
    session.add(message)
    session.commit()
    session.refresh(message)
    record_audit_event(
        session,
        org_id=message.org_id,
        system_id=message.system_id,
        actor_type="platform",
        event_type="message.enqueue_failed",
        target_type="message",
        target_id=message.id,
        input={"message_type": message.message_type},
        output={"error": error},
        commit=True,
    )
    return message


def enqueue_message_processing(message_id: int) -> None:
    from app.workers.tasks import process_open_message

    process_open_message.delay(message_id)


def process_message_by_id(message_id: int, db_engine=None) -> dict:
    with Session(db_engine or engine) as session:
        message = session.exec(
            select(SystemMessage).where(SystemMessage.id == message_id)
        ).first()
        if not message:
            return {"ok": False, "message_id": message_id, "error": "message_not_found"}

        try:
            processed = enrich_message(session, message)
            return {"ok": True, "message_id": processed.id}
        except Exception as exc:
            related = dict(message.related or {})
            related["processing_status"] = "failed"
            related["processing_failed_at"] = datetime.now(timezone.utc).isoformat()
            related["processing_error"] = str(exc)
            message.related = related
            session.add(message)
            session.commit()
            record_audit_event(
                session,
                org_id=message.org_id,
                system_id=message.system_id,
                actor_type="platform",
                event_type="message.processing_failed",
                target_type="message",
                target_id=message.id,
                input={"message_type": message.message_type},
                output={"error": str(exc)},
                commit=True,
            )
            return {"ok": False, "message_id": message.id, "error": str(exc)}


def enrich_message(session: Session, message: SystemMessage) -> SystemMessage:
    related = dict(message.related or {})
    context = related.get("context", {}) if isinstance(related.get("context", {}), dict) else {}
    service = context.get("service") or context.get("component") or ""
    target = f"「{service}」" if service else "相关服务"

    if message.message_type == "alert":
        message.diagnosis = _alert_diagnosis(message, target)
        message.suggestion = _alert_suggestions(target)
    elif message.message_type in {"daily_report", "weekly_report", "monthly_report"}:
        if related.get("processing_kind") == "production_report":
            message.diagnosis, message.suggestion = _production_report_with_ai(session, message)
        else:
            message.diagnosis = _report_diagnosis(message)
            message.suggestion = _report_suggestions(message.message_type)
    else:
        message.diagnosis = "平台已对业务消息完成基础加工，可在消息中心统一跟进。"
        message.suggestion = ["确认消息内容是否需要处理。", "如涉及异常，请进入对应系统详情页查看健康、指标和诊断。"]

    related["need_llm_process"] = bool(related.get("need_llm_process"))
    related["processing_mode"] = "rules"
    related["processing_status"] = "done"
    related["processed_at"] = datetime.now(timezone.utc).isoformat()
    message.related = related
    session.add(message)
    session.commit()
    session.refresh(message)
    record_audit_event(
        session,
        org_id=message.org_id,
        system_id=message.system_id,
        actor_type="platform",
        event_type="message.processed",
        target_type="message",
        target_id=message.id,
        input={"message_type": message.message_type, "processing_mode": "rules"},
        output={"diagnosis": message.diagnosis, "suggestion_count": len(message.suggestion)},
        commit=True,
    )
    if related.get("notify_after_processing"):
        _deliver_processed_message(session, message)
    return message


def _deliver_processed_message(session: Session, message: SystemMessage) -> None:
    try:
        from app.services.notifications.alerts import deliver_message_notifications
        from app.services.systems.service import require_system

        system = require_system(session, message.system_id, message.org_id)
        deliver_message_notifications(
            session,
            system,
            message,
            actor_id=str((message.related or {}).get("token_id") or "openapi"),
        )
    except Exception as exc:
        related = dict(message.related or {})
        related["notification_error"] = str(exc)
        message.related = related
        session.add(message)
        session.commit()


def _production_report_with_ai(session: Session, message: SystemMessage) -> tuple[str, list[str]]:
    from app.agent.diagnostics.runner import diagnose_with_details
    from app.repositories.systems import list_enabled_services_for_system
    from app.services.descriptors.builder import system_to_descriptor
    from app.services.systems.service import require_system

    report_name = {
        "daily_report": "生产日报",
        "weekly_report": "生产周报",
        "monthly_report": "生产月报",
    }.get(message.message_type, "生产报告")
    related = message.related or {}
    system = require_system(session, message.system_id, message.org_id)
    descriptor = system_to_descriptor(system, list_enabled_services_for_system(session, system.id))
    prompt = (
        f"请基于外部业务系统提交的生产数据生成{report_name}。要求：\n"
        "1. 先给出整体结论；2. 总结关键产量/成功失败/异常趋势；"
        "3. 结合系统知识库和上下文指出风险；4. 给出下一步处理建议；"
        "5. 不要编造数据，没有的数据明确说明。\n\n"
        f"报告标题：{message.title}\n"
        f"摘要：{message.summary}\n"
        f"生产数据：\n{message.content}\n\n"
        f"上下文：{related.get('context', {})}"
    )
    try:
        run = diagnose_with_details(
            descriptor,
            prompt,
            org_id=message.org_id,
            system_id=message.system_id,
            trace_question=f"外部生产数据加工：{message.title}",
            skill_steps=(
                "## 排查剧本：生产报告加工\n"
                "1. 读取外部系统提交的生产数据。\n"
                "2. 结合知识库判断关键业务指标是否异常。\n"
                "3. 输出日报/周报结论、风险、建议和待确认事项。"
            ),
        )
        return run.answer, [
            "关注报告中的异常趋势和失败项。",
            "如报告指出风险，请进入系统监控页结合指标、日志继续确认。",
            "必要时在 AI 诊断中追问具体生产批次或失败原因。",
        ]
    except Exception as exc:
        return (
            f"{report_name}已接收，但模型加工失败：{exc}。平台已保留原始生产数据摘要，可稍后重新分析。",
            _report_suggestions(message.message_type),
        )


def _alert_diagnosis(message: SystemMessage, target: str) -> str:
    severity = "高优先级" if message.severity in {"critical", "error"} else "普通优先级"
    return f"业务系统上报{severity}告警，问题集中在{target}。平台已记录上下文，建议结合近期健康状态和指标确认影响范围。"


def _alert_suggestions(target: str) -> list[str]:
    return [
        f"先确认{target}当前健康状态是否仍异常。",
        "查看最近一次健康上报、监控指标和相关日志，判断是否为持续故障。",
        "如果影响业务，及时确认告警并在 AI 诊断页发起进一步分析。",
    ]


def _report_diagnosis(message: SystemMessage) -> str:
    report_name = {
        "daily_report": "日报",
        "weekly_report": "周报",
        "monthly_report": "月报",
    }.get(message.message_type, "报告")
    return f"业务系统提交{report_name}，平台已完成基础加工并纳入统一消息中心，可用于后续运行复盘。"


def _report_suggestions(message_type: str) -> list[str]:
    period = {
        "daily_report": "当天",
        "weekly_report": "本周",
        "monthly_report": "本月",
    }.get(message_type, "当前周期")
    return [
        f"检查{period}是否存在异常、失败率升高或数据缺口。",
        "如报告提到异常趋势，请进入系统监控页查看对应指标。",
        "必要时在 AI 诊断中追问具体异常项，生成处理建议。",
    ]

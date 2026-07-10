"""Lightweight message enrichment for OpenAPI submissions."""
from datetime import datetime, timezone

from sqlmodel import Session

from app.models.messages import SystemMessage
from app.services.audit import record_audit_event


def enrich_message(session: Session, message: SystemMessage) -> SystemMessage:
    related = dict(message.related or {})
    context = related.get("context", {}) if isinstance(related.get("context", {}), dict) else {}
    service = context.get("service") or context.get("component") or ""
    target = f"「{service}」" if service else "相关服务"

    if message.message_type == "alert":
        message.diagnosis = _alert_diagnosis(message, target)
        message.suggestion = _alert_suggestions(target)
    elif message.message_type in {"daily_report", "monthly_report"}:
        message.diagnosis = _report_diagnosis(message)
        message.suggestion = _report_suggestions(message.message_type)
    else:
        message.diagnosis = "平台已对业务消息完成基础加工，可在消息中心统一跟进。"
        message.suggestion = ["确认消息内容是否需要处理。", "如涉及异常，请进入对应系统详情页查看健康、指标和诊断。"]

    related["need_llm_process"] = bool(related.get("need_llm_process"))
    related["processing_mode"] = "rules"
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
    return message


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
    report_name = "月报" if message.message_type == "monthly_report" else "日报"
    return f"业务系统提交{report_name}，平台已完成基础加工并纳入统一消息中心，可用于后续运行复盘。"


def _report_suggestions(message_type: str) -> list[str]:
    period = "本月" if message_type == "monthly_report" else "当天"
    return [
        f"检查{period}是否存在异常、失败率升高或数据缺口。",
        "如报告提到异常趋势，请进入系统监控页查看对应指标。",
        "必要时在 AI 诊断中追问具体异常项，生成处理建议。",
    ]

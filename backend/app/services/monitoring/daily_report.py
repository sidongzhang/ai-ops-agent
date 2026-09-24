"""每日健康日报：汇总昨日告警、恢复、诊断与当前健康状态，生成消息并推送。"""
import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlmodel import Session, select

from app.core.database import engine
from app.models.diagnostics import DiagnosisReport
from app.models.messages import SystemMessage
from app.models.systems import MonitoredSystem, Service
from app.services.audit import record_audit_event
from app.services.notifications.alerts import deliver_message_notifications

log = logging.getLogger(__name__)
REPORT_TZ = ZoneInfo("Asia/Shanghai")


def _window(report_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(report_date, time.min, tzinfo=REPORT_TZ)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _enabled(system: MonitoredSystem) -> bool:
    config = (system.infra or {}).get("daily_report") or {}
    return bool(config.get("enabled", False))


def _summarize(session: Session, system: MonitoredSystem, report_date: date) -> dict:
    start, end = _window(report_date)
    messages = session.exec(
        select(SystemMessage).where(
            SystemMessage.system_id == system.id,
            SystemMessage.org_id == system.org_id,
            SystemMessage.created_at >= start,
            SystemMessage.created_at < end,
        ).order_by(SystemMessage.created_at.asc())
    ).all()
    diagnoses = session.exec(
        select(DiagnosisReport).where(
            DiagnosisReport.system_id == system.id,
            DiagnosisReport.org_id == system.org_id,
            DiagnosisReport.created_at >= start,
            DiagnosisReport.created_at < end,
            DiagnosisReport.status == "success",
        ).order_by(DiagnosisReport.created_at.asc())
    ).all()
    services = session.exec(
        select(Service).where(Service.system_id == system.id, Service.enabled == True)  # noqa: E712
    ).all()
    health = (system.last_health or {}).get("services", [])
    health_by_name = {str(s.get("name")): s for s in health}
    service_health = [
        {"name": svc.name, "ok": health_by_name.get(svc.name, {}).get("ok"),
         "detail": str(health_by_name.get(svc.name, {}).get("detail", "未知"))[:180]}
        for svc in services
    ]
    alerts = [m for m in messages if m.message_type == "alert"]
    recovered = [m for m in alerts if m.status == "resolved"]
    active = [m for m in alerts if m.status != "resolved"]
    summary = {
        "system": system.name,
        "date": report_date.isoformat(),
        "service_count": len(services),
        "healthy_count": sum(1 for s in service_health if s["ok"] is True),
        "unhealthy_count": sum(1 for s in service_health if s["ok"] is False),
        "unknown_count": sum(1 for s in service_health if s["ok"] is None),
        "services": service_health,
        "alert_count": len(alerts),
        "active_alert_count": len(active),
        "recovered_alert_count": len(recovered),
        "alerts": [{"title": m.title, "summary": m.summary, "status": m.status,
                    "severity": m.severity} for m in alerts[:20]],
        "diagnosis_count": len(diagnoses),
        "diagnoses": [{"question": d.question[:200], "answer": d.answer[:500],
                       "created_at": d.created_at.isoformat() if d.created_at else ""}
                      for d in diagnoses[:10]],
    }
    return summary


def _generate_text(data: dict) -> str:
    """单次模型调用生成日报；失败时降级成基于原始统计的确定性摘要。"""
    prompt = (
        "你是 AIOps 值班日报助手。请只根据下列 JSON 事实生成一份简洁中文日报，严禁编造。\n"
        "格式：整体结论（1-2句）/ 昨日告警与恢复 / 当前服务状态 / 诊断发现 / 今日关注建议。\n"
        "没有数据就明确写‘昨日无告警/无诊断记录’；当前健康状态 unknown 不得说正常。\n"
        "每条建议必须对应已知告警或状态，不要给泛化运维套话。\n\n"
        f"数据：{data}"
    )
    try:
        from pydantic_ai import Agent
        from app.agent.diagnostics.models import default_model

        agent = Agent(default_model(), output_type=str, instructions="严格依据输入数据，不得推断未提供事实。")
        result = agent.run_sync(prompt, model=default_model(), retries=1)
        return str(result.output).strip()[:6000]
    except Exception as exc:  # noqa: BLE001
        log.warning("[daily-report] AI 汇总失败，降级规则摘要: %s", exc)
        return (
            f"**整体结论**：昨日系统「{data['system']}」巡检覆盖 {data['service_count']} 个服务；"
            f"当前健康 {data['healthy_count']} 正常、{data['unhealthy_count']} 异常、"
            f"{data['unknown_count']} 未知。\n\n"
            f"**昨日告警与恢复**：共 {data['alert_count']} 条，"
            f"{data['recovered_alert_count']} 条已恢复，{data['active_alert_count']} 条仍未解决。\n\n"
            f"**诊断发现**：完成 {data['diagnosis_count']} 次成功诊断。\n"
            "**今日关注建议**：优先处理仍未解决的告警，并确认状态未知的服务。"
        )


def run_daily_health_reports(report_date: date | None = None) -> dict:
    """为显式启用日报的系统生成并推送日报；幂等（同系统同日期只生成一次）。"""
    report_date = report_date or (datetime.now(REPORT_TZ).date() - timedelta(days=1))
    created = skipped = errors = 0
    with Session(engine) as session:
        systems = session.exec(select(MonitoredSystem)).all()
        for system in systems:
            try:
                if not _enabled(system):
                    skipped += 1
                    continue
                prior = session.exec(
                    select(SystemMessage).where(
                        SystemMessage.system_id == system.id,
                        SystemMessage.org_id == system.org_id,
                        SystemMessage.message_type == "daily_report",
                        SystemMessage.source == "scheduled_daily_report",
                    )
                ).all()
                if any((m.related or {}).get("report_date") == report_date.isoformat() for m in prior):
                    skipped += 1
                    continue

                data = _summarize(session, system, report_date)
                content = _generate_text(data)
                msg = SystemMessage(
                    org_id=system.org_id,
                    system_id=system.id,
                    message_type="daily_report",
                    severity="info" if not data["unhealthy_count"] and not data["active_alert_count"] else "warning",
                    title=f"系统「{system.name}」健康日报 · {report_date.isoformat()}",
                    summary=(f"健康 {data['healthy_count']}/{data['service_count']} 服务；"
                             f"昨日告警 {data['alert_count']} 条（恢复 {data['recovered_alert_count']}，"
                             f"未解决 {data['active_alert_count']}）；诊断 {data['diagnosis_count']} 次"),
                    content=content,
                    diagnosis=content,
                    suggestion=[],
                    source="scheduled_daily_report",
                    related={"report_date": report_date.isoformat(), "facts": data},
                )
                session.add(msg)
                session.flush()
                deliver_message_notifications(session, system, msg, actor_id="daily-report")
                record_audit_event(
                    session, org_id=system.org_id, system_id=system.id,
                    event_type="daily_report.generated", actor_type="system",
                    actor_id="daily-report", target_type="message", target_id=msg.id,
                    output={"report_date": report_date.isoformat(), "alert_count": data["alert_count"]},
                    commit=False,
                )
                session.commit()
                created += 1
                log.info("[daily-report] system=%s date=%s message_id=%s", system.id, report_date, msg.id)
            except Exception as exc:  # noqa: BLE001 - 单系统失败不阻断其他租户
                session.rollback()
                errors += 1
                log.exception("[daily-report] system=%s 生成失败: %s", system.id, exc)
    return {"created": created, "skipped": skipped, "errors": errors,
            "report_date": report_date.isoformat()}

"""Compute management-facing efficiency metrics from persisted product events."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.models.audit import AuditLog
from app.models.messages import SystemMessage
from app.models.systems import MonitoredSystem
from app.schemas.analytics import EfficiencyAnalyticsOut, EfficiencyTrendPoint, SystemEfficiencyRow


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _minutes(start: datetime, end: datetime | None) -> float | None:
    if not end:
        return None
    return max(0.0, (_utc(end) - _utc(start)).total_seconds() / 60)


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 1) if denominator else 0


def get_efficiency_analytics(
    session: Session,
    org_id: int,
    *,
    days: int = 30,
    system_id: int | None = None,
) -> EfficiencyAnalyticsOut:
    days = max(1, min(days, 90))
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    message_query = select(SystemMessage).where(
        SystemMessage.org_id == org_id,
        SystemMessage.created_at >= since,
    )
    audit_query = select(AuditLog).where(
        AuditLog.org_id == org_id,
        AuditLog.created_at >= since,
    )
    if system_id is not None:
        message_query = message_query.where(SystemMessage.system_id == system_id)
        audit_query = audit_query.where(AuditLog.system_id == system_id)

    messages = list(session.exec(message_query))
    audits = list(session.exec(audit_query))
    alerts = [message for message in messages if message.message_type == "alert"]
    resolved = [message for message in alerts if message.resolved_at]
    ack_minutes = [value for message in alerts if (value := _minutes(message.created_at, message.ack_at)) is not None]
    resolution_minutes = [
        value for message in resolved if (value := _minutes(message.created_at, message.resolved_at)) is not None
    ]

    diagnosis_logs = [
        audit for audit in audits if audit.event_type in {"diagnosis.completed", "diagnosis.failed"}
    ]
    diagnosis_successes = [audit for audit in diagnosis_logs if audit.event_type == "diagnosis.completed"]
    diagnosis_durations = [
        float(audit.output.get("duration_ms", 0)) / 1000
        for audit in diagnosis_successes
        if audit.output.get("duration_ms") is not None
    ]
    evidence_diagnoses = sum(
        1 for audit in diagnosis_successes if audit.output.get("evidence_sources")
    )

    external_deliveries = [
        channel
        for message in alerts
        for channel in (message.channels or [])
        if channel.get("type") != "web"
    ]
    failed_deliveries = [channel for channel in external_deliveries if channel.get("status") == "failed"]
    notification_retries = sum(max(0, int(channel.get("attempts") or 1) - 1) for channel in external_deliveries)

    duplicate_events = [audit for audit in audits if audit.event_type.endswith(".duplicate")]
    created_open_events = [
        audit
        for audit in audits
        if audit.event_type.startswith("openapi.") and audit.event_type.endswith(".created")
    ]
    probe_failures = [
        audit
        for audit in audits
        if audit.event_type == "service.probe_tested" and audit.status == "failed"
    ]
    workflow_events = [
        audit
        for audit in audits
        if audit.event_type in {"workflow.executed", "workflow.execution_failed"}
    ]
    successful_workflows = [audit for audit in workflow_events if audit.event_type == "workflow.executed" and audit.status != "error"]

    system_records = list(
        session.exec(
            select(MonitoredSystem).where(
                MonitoredSystem.org_id == org_id,
                *([MonitoredSystem.id == system_id] if system_id is not None else []),
            )
        )
    )
    system_names = {system.id: system.name for system in system_records}
    alerts_by_system: dict[int, list[SystemMessage]] = defaultdict(list)
    for alert in alerts:
        alerts_by_system[alert.system_id].append(alert)

    system_rows = []
    for current_id, name in system_names.items():
        current_alerts = alerts_by_system.get(current_id, [])
        current_resolved = [item for item in current_alerts if item.resolved_at]
        current_resolution_minutes = [
            value
            for item in current_resolved
            if (value := _minutes(item.created_at, item.resolved_at)) is not None
        ]
        system_rows.append(
            SystemEfficiencyRow(
                system_id=current_id,
                system_name=name,
                alerts=len(current_alerts),
                unresolved=len(current_alerts) - len(current_resolved),
                resolved_rate_pct=_rate(len(current_resolved), len(current_alerts)),
                avg_resolution_minutes=_average(current_resolution_minutes),
            )
        )
    system_rows.sort(key=lambda item: (-item.unresolved, -item.alerts, item.system_name))

    trend_map = {
        (now - timedelta(days=offset)).date(): {"alerts": 0, "resolved": 0, "diagnoses": 0}
        for offset in range(days - 1, -1, -1)
    }
    for alert in alerts:
        day = _utc(alert.created_at).date()
        if day in trend_map:
            trend_map[day]["alerts"] += 1
        if alert.resolved_at:
            resolved_day = _utc(alert.resolved_at).date()
            if resolved_day in trend_map:
                trend_map[resolved_day]["resolved"] += 1
    for audit in diagnosis_logs:
        day = _utc(audit.created_at).date()
        if day in trend_map:
            trend_map[day]["diagnoses"] += 1

    return EfficiencyAnalyticsOut(
        days=days,
        since=since,
        alerts_total=len(alerts),
        alerts_resolved=len(resolved),
        alerts_unresolved=len(alerts) - len(resolved),
        alert_resolution_rate_pct=_rate(len(resolved), len(alerts)),
        avg_ack_minutes=_average(ack_minutes),
        avg_resolution_minutes=_average(resolution_minutes),
        diagnoses_total=len(diagnosis_logs),
        diagnosis_success_rate_pct=_rate(len(diagnosis_successes), len(diagnosis_logs)),
        avg_diagnosis_seconds=_average(diagnosis_durations),
        diagnosis_evidence_rate_pct=_rate(evidence_diagnoses, len(diagnosis_successes)),
        notification_deliveries=len(external_deliveries),
        notification_failures=len(failed_deliveries),
        notification_success_rate_pct=_rate(
            len(external_deliveries) - len(failed_deliveries),
            len(external_deliveries),
        ),
        notification_retries=notification_retries,
        duplicate_requests_blocked=len(duplicate_events),
        duplicate_request_rate_pct=_rate(
            len(duplicate_events),
            len(duplicate_events) + len(created_open_events),
        ),
        invalid_configs_blocked=len(probe_failures),
        workflow_executions=len(workflow_events),
        workflow_success_rate_pct=_rate(len(successful_workflows), len(workflow_events)),
        trend=[EfficiencyTrendPoint(date=day, **values) for day, values in trend_map.items()],
        systems=system_rows,
    )

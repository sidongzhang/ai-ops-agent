"""Scheduled health-check orchestration for Celery workers."""
import logging
import time
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.core.config import settings
from app.core.database import engine
from app.models.collectors import Collector
from app.models.systems import MonitoredSystem, Service
from app.services.descriptors.builder import system_to_descriptor
from app.services.descriptors.health import collect_health
from app.services.notifications.alerts import alert_if_needed
from app.services.systems.service import get_monitoring_config

log = logging.getLogger(__name__)

COLLECTOR_STALE_MULTIPLIER = 2


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def services_of(session: Session, system_id: int) -> list[Service]:
    return list(session.exec(select(Service).where(Service.system_id == system_id)))


def has_fresh_collector(system: MonitoredSystem, session: Session) -> bool:
    interval = get_monitoring_config(system).interval_seconds
    now = utcnow()
    collectors = session.exec(
        select(Collector).where(Collector.system_id == system.id)
    ).all()
    return any(
        collector.last_seen
        and (now - collector.last_seen.replace(tzinfo=timezone.utc)).total_seconds()
        < interval * COLLECTOR_STALE_MULTIPLIER
        for collector in collectors
    )


def is_due(system: MonitoredSystem) -> bool:
    last = system.last_report_at
    if not last:
        return True
    elapsed = (utcnow() - last.replace(tzinfo=timezone.utc)).total_seconds()
    return elapsed >= get_monitoring_config(system).interval_seconds


def run_scheduled_health_checks() -> dict:
    started_at = time.monotonic()
    checked = skipped = errors = 0

    with Session(engine) as session:
        systems = session.exec(select(MonitoredSystem)).all()
        for system in systems:
            try:
                monitoring = get_monitoring_config(system)
                if not monitoring.enabled:
                    skipped += 1
                    continue
                if not is_due(system):
                    skipped += 1
                    continue
                if has_fresh_collector(system, session):
                    skipped += 1
                    log.debug(f"[check] 系统「{system.name}」由采集器负责，跳过")
                    continue

                services = services_of(session, system.id)
                if not services:
                    skipped += 1
                    continue

                descriptor = system_to_descriptor(system, services)
                results = collect_health(descriptor)
                alert_if_needed(system, results, session)

                system.last_health = {"services": results}
                system.last_report_at = utcnow()
                session.add(system)
                checked += 1
            except Exception as exc:
                errors += 1
                log.error(f"[check] 系统「{system.name}」巡检出错: {exc}", exc_info=True)

        session.commit()

    elapsed = time.monotonic() - started_at
    log.info(
        f"[check] 本轮完成：checked={checked} skipped={skipped} errors={errors} "
        f"elapsed={elapsed:.2f}s"
    )
    return {"checked": checked, "skipped": skipped, "errors": errors}

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

log = logging.getLogger(__name__)

COLLECTOR_STALE_MULTIPLIER = 2


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def services_of(session: Session, system_id: int) -> list[Service]:
    return list(session.exec(select(Service).where(Service.system_id == system_id)))


def has_fresh_collector(system: MonitoredSystem, session: Session) -> bool:
    collector = session.exec(
        select(Collector).where(Collector.system_id == system.id)
    ).first()
    if not collector or not system.last_report_at:
        return False
    elapsed = (utcnow() - system.last_report_at.replace(tzinfo=timezone.utc)).total_seconds()
    return elapsed < settings.health_check_interval * COLLECTOR_STALE_MULTIPLIER


def run_scheduled_health_checks() -> dict:
    started_at = time.monotonic()
    checked = skipped = errors = 0

    with Session(engine) as session:
        systems = session.exec(select(MonitoredSystem)).all()
        for system in systems:
            try:
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

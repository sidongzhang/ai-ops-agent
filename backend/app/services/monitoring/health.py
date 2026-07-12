"""Application service for system health resolution."""
from sqlmodel import Session

from app.repositories.collectors import get_first_collector_for_system
from app.repositories.systems import list_enabled_services_for_system, list_services_for_system
from app.schemas import HealthItem, SystemHealth
from app.services.descriptors.builder import system_to_descriptor
from app.services.descriptors.health import collect_health
from app.services.systems.service import require_system
from app.services.systems.service import get_monitoring_config
from datetime import datetime, timezone


def get_system_health(session: Session, system_id: int, org_id: int) -> SystemHealth:
    system = require_system(session, system_id, org_id)
    collector = get_first_collector_for_system(session, system.id)
    if collector:
        stale_after = get_monitoring_config(system).interval_seconds * 2
        age = (
            (datetime.now(timezone.utc) - system.last_report_at.replace(tzinfo=timezone.utc)).total_seconds()
            if system.last_report_at else None
        )
        if not system.local and (age is None or age >= stale_after):
            return SystemHealth(
                system_id=system.id,
                name=system.name,
                healthy=False,
                services=[HealthItem(name="远程采集器", ok=False, detail="采集器长时间未上报，请检查采集器进程和网络", connector="collector")],
                source="collector_offline",
                reported_at=system.last_report_at.isoformat() if system.last_report_at else None,
            )
        registered_services = list_services_for_system(session, system.id)
        enabled_names = (
            {service.name for service in registered_services if service.enabled}
            if registered_services else None
        )
        items = [
            HealthItem(**item)
            for item in system.last_health.get("services", [])
            if enabled_names is None or item.get("name") in enabled_names
        ]
        return SystemHealth(
            system_id=system.id,
            name=system.name,
            healthy=all(item.ok for item in items) if items else True,
            services=items,
            source="collector",
            reported_at=system.last_report_at.isoformat(),
        )

    descriptor = system_to_descriptor(system, list_enabled_services_for_system(session, system.id))
    health = collect_health(descriptor)
    return SystemHealth(
        system_id=system.id,
        name=system.name,
        healthy=all(item["ok"] for item in health) if health else True,
        services=[HealthItem(**item) for item in health],
        source="direct",
    )

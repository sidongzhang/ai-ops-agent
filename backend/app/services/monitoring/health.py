"""Application service for system health resolution."""
from sqlmodel import Session

from app.repositories.collectors import get_first_collector_for_system
from app.repositories.systems import list_services_for_system
from app.schemas import HealthItem, SystemHealth
from app.services.descriptors.builder import system_to_descriptor
from app.services.descriptors.health import collect_health
from app.services.systems.service import require_system


def get_system_health(session: Session, system_id: int, org_id: int) -> SystemHealth:
    system = require_system(session, system_id, org_id)
    collector = get_first_collector_for_system(session, system.id)
    if collector and system.last_report_at:
        items = [HealthItem(**item) for item in system.last_health.get("services", [])]
        return SystemHealth(
            system_id=system.id,
            name=system.name,
            healthy=all(item.ok for item in items) if items else True,
            services=items,
            source="collector",
            reported_at=system.last_report_at.isoformat(),
        )

    descriptor = system_to_descriptor(system, list_services_for_system(session, system.id))
    health = collect_health(descriptor)
    return SystemHealth(
        system_id=system.id,
        name=system.name,
        healthy=all(item["ok"] for item in health) if health else True,
        services=[HealthItem(**item) for item in health],
        source="direct",
    )

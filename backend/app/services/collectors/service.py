"""Application services for collector CRUD and agent reporting."""
from datetime import datetime, timezone

from sqlmodel import Session

from app.core.security import generate_collector_key, hash_collector_key
from app.models.collectors import Collector
from app.repositories.collectors import list_collectors_for_system
from app.repositories.systems import list_enabled_services_for_system
from app.schemas import CollectorConfig, CollectorCreate, CollectorCreated, CollectorOut, CollectorReport
from app.services.descriptors.builder import system_to_descriptor
from app.services.systems.service import require_system


def create_collector(session: Session, system_id: int, org_id: int, body: CollectorCreate) -> CollectorCreated:
    system = require_system(session, system_id, org_id)
    key = generate_collector_key()
    collector = Collector(
        org_id=org_id,
        system_id=system.id,
        name=body.name,
        token_hash=hash_collector_key(key),
    )
    session.add(collector)
    session.commit()
    session.refresh(collector)
    return CollectorCreated(
        id=collector.id,
        name=collector.name,
        system_id=system.id,
        collector_key=key,
    )


def list_collectors(session: Session, system_id: int, org_id: int) -> list[CollectorOut]:
    system = require_system(session, system_id, org_id)
    return [
        CollectorOut(
            id=collector.id,
            name=collector.name,
            system_id=collector.system_id,
            last_seen=collector.last_seen.isoformat() if collector.last_seen else None,
        )
        for collector in list_collectors_for_system(session, system.id)
    ]


def get_collector_config(session: Session, collector: Collector) -> CollectorConfig:
    system = require_system(session, collector.system_id, collector.org_id)
    services = list_enabled_services_for_system(session, system.id)
    descriptor = system_to_descriptor(system, services)
    return CollectorConfig(
        system_id=system.id,
        name=system.name,
        local=system.local,
        infra=system.infra or {},
        services=descriptor["services"],
    )


def record_collector_report(session: Session, collector: Collector, body: CollectorReport) -> dict:
    now = datetime.now(timezone.utc)
    system = require_system(session, collector.system_id, collector.org_id)
    system.last_health = {"services": [service.model_dump() for service in body.services]}
    system.last_report_at = now
    collector.last_seen = now
    session.add(system)
    session.add(collector)
    session.commit()
    return {"ok": True, "received": len(body.services)}

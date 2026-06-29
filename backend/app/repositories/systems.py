"""Persistence helpers for monitored systems and their services."""
from sqlmodel import Session, select

from ..models.systems import MonitoredSystem, Service


def get_system_for_org(session: Session, system_id: int, org_id: int) -> MonitoredSystem | None:
    return session.exec(
        select(MonitoredSystem).where(
            MonitoredSystem.id == system_id,
            MonitoredSystem.org_id == org_id,
        )
    ).first()


def get_system_by_key(session: Session, org_id: int, key: str) -> MonitoredSystem | None:
    return session.exec(
        select(MonitoredSystem).where(
            MonitoredSystem.org_id == org_id,
            MonitoredSystem.key == key,
        )
    ).first()


def list_systems_for_org(session: Session, org_id: int) -> list[MonitoredSystem]:
    return list(
        session.exec(select(MonitoredSystem).where(MonitoredSystem.org_id == org_id))
    )


def list_services_for_system(session: Session, system_id: int) -> list[Service]:
    return list(session.exec(select(Service).where(Service.system_id == system_id)))


def get_service_for_system(session: Session, system_id: int, service_id: int) -> Service | None:
    service = session.get(Service, service_id)
    if not service or service.system_id != system_id:
        return None
    return service

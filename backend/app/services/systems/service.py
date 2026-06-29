"""Application services for monitored systems."""
from sqlmodel import Session

from app.core.security import (
    decrypt_sensitive_fields,
    encrypt_sensitive_fields,
    mask_sensitive_fields,
)
from app.models.systems import MonitoredSystem, Service
from app.repositories.systems import (
    get_service_for_system,
    get_system_by_key,
    get_system_for_org,
    list_services_for_system,
    list_systems_for_org,
)
from app.schemas import NotifyConfig, ServiceIn, ServiceOut, SystemCreate, SystemOut
from app.services.notifications.config import merge_notify_config


def require_system(session: Session, system_id: int, org_id: int) -> MonitoredSystem:
    system = get_system_for_org(session, system_id, org_id)
    if not system:
        raise LookupError("系统不存在")
    return system


def to_system_out(system: MonitoredSystem, services: list[Service]) -> SystemOut:
    return SystemOut(
        id=system.id,
        org_id=system.org_id,
        key=system.key,
        name=system.name,
        local=system.local,
        notify=mask_sensitive_fields(system.notify),
        infra=mask_sensitive_fields(system.infra),
        services=[
            ServiceOut(
                id=service.id,
                name=service.name,
                connector=service.connector,
                config=mask_sensitive_fields(service.config),
            )
            for service in services
        ],
    )


def create_system(session: Session, org_id: int, body: SystemCreate) -> SystemOut:
    if get_system_by_key(session, org_id, body.key):
        raise ValueError(f"key「{body.key}」在本组织下已存在")

    system = MonitoredSystem(
        org_id=org_id,
        key=body.key,
        name=body.name,
        local=body.local,
        notify=body.notify,
        infra=encrypt_sensitive_fields(body.infra),
    )
    session.add(system)
    session.commit()
    session.refresh(system)

    services: list[Service] = []
    for item in body.services:
        service = Service(
            system_id=system.id,
            name=item.name,
            connector=item.connector,
            config=encrypt_sensitive_fields(item.config),
        )
        session.add(service)
        services.append(service)
    session.commit()
    for service in services:
        session.refresh(service)
    return to_system_out(system, services)


def list_systems(session: Session, org_id: int) -> list[SystemOut]:
    systems = list_systems_for_org(session, org_id)
    return [to_system_out(system, list_services_for_system(session, system.id)) for system in systems]


def get_system(session: Session, system_id: int, org_id: int) -> SystemOut:
    system = require_system(session, system_id, org_id)
    return to_system_out(system, list_services_for_system(session, system.id))


def add_service(session: Session, system_id: int, org_id: int, body: ServiceIn) -> ServiceOut:
    system = require_system(session, system_id, org_id)
    service = Service(
        system_id=system.id,
        name=body.name,
        connector=body.connector,
        config=encrypt_sensitive_fields(body.config),
    )
    session.add(service)
    session.commit()
    session.refresh(service)
    return ServiceOut(
        id=service.id,
        name=service.name,
        connector=service.connector,
        config=mask_sensitive_fields(service.config),
    )


def delete_service(session: Session, system_id: int, service_id: int, org_id: int) -> None:
    require_system(session, system_id, org_id)
    service = get_service_for_system(session, system_id, service_id)
    if not service:
        raise LookupError("服务不存在")
    session.delete(service)
    session.commit()


def update_notify(session: Session, system_id: int, org_id: int, body: NotifyConfig) -> dict:
    system = require_system(session, system_id, org_id)
    merged = merge_notify_config(body, system.notify)
    system.notify = encrypt_sensitive_fields(merged)
    session.add(system)
    session.commit()
    session.refresh(system)
    return mask_sensitive_fields(system.notify)


def get_decrypted_notify(session: Session, system_id: int, org_id: int) -> tuple[str, dict]:
    system = require_system(session, system_id, org_id)
    return system.name, decrypt_sensitive_fields(system.notify or {})

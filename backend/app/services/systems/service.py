"""系统注册 CRUD 与读模型（服务生命周期/配置见 lifecycle.py、configuration.py）。

本模块保留原有 import 路径：拆出的函数在此 re-export，历史调用方无需改动。
"""
from datetime import datetime, timezone

from sqlmodel import Session

from app.core.security import encrypt_sensitive_fields, mask_sensitive_fields
from app.models.auth import User
from app.models.systems import MonitoredSystem, Service
from app.repositories.systems import (
    delete_system_for_org,
    get_system_by_key,
    list_services_for_system,
    list_systems_for_org,
)
from app.schemas import RestartPolicyOut, SystemCreate, SystemOut
from app.services.systems.access import get_primary_collector, require_system
from app.services.systems.configuration import (
    get_decrypted_notify,
    get_monitoring_config,
    get_restart_policy,
    update_monitoring_config,
    update_notify,
    update_restart_policy,
)
from app.services.systems.lifecycle import (
    probe_service as _probe_service,
    add_service,
    delete_service,
    execute_registered_service_restart,
    enable_service_draft,
    test_service_draft,
    update_service_draft,
)
from app.services.systems.restart import (
    build_default_restart_policy,
    build_restart_capability,
    can_manage_restart_policy,
    has_restart_permission,
    normalize_restart_policy,
)
from app.services.systems.views import service_out as _service_out


__all__ = [
    "require_system",
    "get_primary_collector",
    "create_system",
    "list_systems",
    "get_system",
    "delete_system",
    "add_service",
    "update_service_draft",
    "test_service_draft",
    "enable_service_draft",
    "delete_service",
    "execute_registered_service_restart",
    "get_monitoring_config",
    "update_monitoring_config",
    "update_notify",
    "get_decrypted_notify",
    "get_restart_policy",
    "update_restart_policy",
]


def _build_system_out(
    session: Session,
    system: MonitoredSystem,
    services: list[Service],
    current_user: User | None = None,
) -> SystemOut:
    policy = normalize_restart_policy(system)
    collector = get_primary_collector(session, system.id)
    monitoring = get_monitoring_config(system)
    return SystemOut(
        id=system.id,
        org_id=system.org_id,
        key=system.key,
        name=system.name,
        local=system.local,
        notify=mask_sensitive_fields(system.notify),
        infra=mask_sensitive_fields(system.infra),
        monitoring=monitoring,
        restart_policy=RestartPolicyOut(
            authorized_user_ids=policy["authorized_user_ids"],
            has_permission=has_restart_permission(current_user, system) if current_user else False,
            can_manage=can_manage_restart_policy(current_user, system) if current_user else False,
        ),
        restart_capability=build_restart_capability(
            system,
            [service for service in services if service.enabled],
            collector,
            current_user,
        ),
        services=[_service_out(service) for service in services],
    )


def create_system(
    session: Session,
    org_id: int,
    body: SystemCreate,
    *,
    creator_user_id: int | None = None,
    current_user: User | None = None,
) -> SystemOut:
    if get_system_by_key(session, org_id, body.key):
        raise ValueError(f"key「{body.key}」在本组织下已存在")

    system = MonitoredSystem(
        org_id=org_id,
        key=body.key,
        name=body.name,
        local=body.local,
        notify=body.notify,
        infra=encrypt_sensitive_fields(body.infra),
        restart_policy=build_default_restart_policy(creator_user_id),
    )

    probe_results: list[tuple[ServiceIn, str]] = []
    for item in body.services:
        if not item.name.strip():
            raise ValueError("服务名称不能为空")
        candidate = Service(
            system_id=0,
            name=item.name.strip(),
            connector=item.connector,
            config=item.config,
            enabled=False,
            probe_status="testing",
        )
        if body.local:
            ok, detail = _probe_service(system, candidate)
            if not ok:
                raise ValueError(f"服务「{candidate.name}」测试未通过：{detail}")
        else:
            ok, detail = False, "等待远程采集器连接后测试"
        probe_results.append((item, detail))

    session.add(system)
    session.commit()
    session.refresh(system)

    services: list[Service] = []
    tested_at = datetime.now(timezone.utc)
    for item, detail in probe_results:
        service = Service(
            system_id=system.id,
            name=item.name.strip(),
            connector=item.connector,
            config=encrypt_sensitive_fields(item.config),
            enabled=body.local,
            probe_status="passed" if body.local else "waiting_collector",
            probe_detail=detail,
            tested_at=tested_at,
        )
        session.add(service)
        services.append(service)
    session.commit()
    for service in services:
        session.refresh(service)
    return _build_system_out(session, system, services, current_user)


def list_systems(session: Session, org_id: int, current_user: User | None = None) -> list[SystemOut]:
    systems = list_systems_for_org(session, org_id)
    return [
        _build_system_out(session, system, list_services_for_system(session, system.id), current_user)
        for system in systems
    ]


def get_system(session: Session, system_id: int, org_id: int, current_user: User | None = None) -> SystemOut:
    system = require_system(session, system_id, org_id)
    return _build_system_out(session, system, list_services_for_system(session, system.id), current_user)


def delete_system(session: Session, system_id: int, org_id: int) -> None:
    system = require_system(session, system_id, org_id)
    services = list_services_for_system(session, system.id)
    if services:
        raise ValueError("系统下还有服务，不能直接销毁")
    if not delete_system_for_org(session, system_id, org_id):
        raise LookupError("系统不存在")

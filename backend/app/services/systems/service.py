"""Application services for monitored systems."""
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.core.security import (
    decrypt_sensitive_fields,
    encrypt_sensitive_fields,
    mask_sensitive_fields,
)
from app.models.auth import User
from app.models.collectors import Collector
from app.models.systems import MonitoredSystem, Service
from app.repositories.systems import (
    delete_system_for_org,
    get_service_for_system,
    get_system_by_key,
    get_system_for_org,
    list_services_for_system,
    list_systems_for_org,
)
from app.schemas import (
    NotifyConfig,
    RestartPolicyOut,
    RestartPolicyUpdate,
    ServiceIn,
    ServiceOut,
    ServiceProbeOut,
    SystemCreate,
    SystemOut,
)
from app.services.descriptors.builder import service_to_descriptor, system_to_descriptor
from app.services.descriptors.runtime import get_connector
from app.services.audit import record_audit_event
from app.services.notifications.config import merge_notify_config
from app.services.systems.restart import (
    build_default_restart_policy,
    build_restart_capability,
    can_manage_restart_policy,
    has_restart_permission,
    normalize_restart_policy,
)


def require_system(session: Session, system_id: int, org_id: int) -> MonitoredSystem:
    system = get_system_for_org(session, system_id, org_id)
    if not system:
        raise LookupError("系统不存在")
    return system


def _get_primary_collector(session: Session, system_id: int) -> Collector | None:
    return session.exec(select(Collector).where(Collector.system_id == system_id)).first()


def _build_system_out(
    session: Session,
    system: MonitoredSystem,
    services: list[Service],
    current_user: User | None = None,
) -> SystemOut:
    policy = normalize_restart_policy(system)
    collector = _get_primary_collector(session, system.id)
    return SystemOut(
        id=system.id,
        org_id=system.org_id,
        key=system.key,
        name=system.name,
        local=system.local,
        notify=mask_sensitive_fields(system.notify),
        infra=mask_sensitive_fields(system.infra),
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


def _service_out(service: Service) -> ServiceOut:
    return ServiceOut(
        id=service.id,
        name=service.name,
        connector=service.connector,
        config=mask_sensitive_fields(service.config),
        enabled=service.enabled,
        probe_status=service.probe_status,
        probe_detail=service.probe_detail,
        tested_at=service.tested_at,
    )


def _probe_service(system: MonitoredSystem, service: Service) -> tuple[bool, str]:
    descriptor = system_to_descriptor(system, [])
    service_descriptor = service_to_descriptor(service)
    try:
        return get_connector(service_descriptor, descriptor).health()
    except Exception as exc:  # noqa: BLE001
        return False, f"检查失败: {exc}"


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
        ok, detail = _probe_service(system, candidate)
        if not ok:
            raise ValueError(f"服务「{candidate.name}」测试未通过：{detail}")
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
            enabled=True,
            probe_status="passed",
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


def add_service(
    session: Session,
    system_id: int,
    org_id: int,
    body: ServiceIn,
    *,
    actor_id: str = "",
) -> ServiceOut:
    system = require_system(session, system_id, org_id)
    if not body.name.strip():
        raise ValueError("服务名称不能为空")
    service = Service(
        system_id=system.id,
        name=body.name.strip(),
        connector=body.connector,
        config=encrypt_sensitive_fields(body.config),
        enabled=False,
        probe_status="draft",
    )
    session.add(service)
    session.flush()
    record_audit_event(
        session,
        org_id=org_id,
        system_id=system.id,
        event_type="service.draft_created",
        actor_type="user",
        actor_id=actor_id,
        target_type="service",
        target_id=service.id,
        input={"name": service.name, "connector": service.connector},
    )
    session.commit()
    session.refresh(service)
    return _service_out(service)


def update_service_draft(
    session: Session,
    system_id: int,
    service_id: int,
    org_id: int,
    body: ServiceIn,
    *,
    actor_id: str = "",
) -> ServiceOut:
    system = require_system(session, system_id, org_id)
    service = get_service_for_system(session, system.id, service_id)
    if not service:
        raise LookupError("服务不存在")
    if service.enabled:
        raise ValueError("已启用服务不能通过草稿接口修改")
    if not body.name.strip():
        raise ValueError("服务名称不能为空")
    service.name = body.name.strip()
    service.connector = body.connector
    service.config = encrypt_sensitive_fields(body.config)
    service.probe_status = "draft"
    service.probe_detail = ""
    service.tested_at = None
    session.add(service)
    record_audit_event(
        session,
        org_id=org_id,
        system_id=system.id,
        event_type="service.draft_updated",
        actor_type="user",
        actor_id=actor_id,
        target_type="service",
        target_id=service.id,
        input={"name": service.name, "connector": service.connector},
    )
    session.commit()
    session.refresh(service)
    return _service_out(service)


def test_service_draft(
    session: Session,
    system_id: int,
    service_id: int,
    org_id: int,
    *,
    actor_id: str = "",
) -> ServiceProbeOut:
    system = require_system(session, system_id, org_id)
    service = get_service_for_system(session, system.id, service_id)
    if not service:
        raise LookupError("服务不存在")
    if service.enabled:
        raise ValueError("服务已启用，无需重复执行启用前测试")
    ok, detail = _probe_service(system, service)
    tested_at = datetime.now(timezone.utc)
    service.probe_status = "passed" if ok else "failed"
    service.probe_detail = detail
    service.tested_at = tested_at
    session.add(service)
    record_audit_event(
        session,
        org_id=org_id,
        system_id=system.id,
        event_type="service.probe_tested",
        actor_type="user",
        actor_id=actor_id,
        target_type="service",
        target_id=service.id,
        status="success" if ok else "failed",
        input={"name": service.name, "connector": service.connector},
        output={"ok": ok, "detail": detail},
    )
    session.commit()
    session.refresh(service)
    return ServiceProbeOut(
        service_id=service.id,
        name=service.name,
        connector=service.connector,
        ok=ok,
        detail=detail,
        tested_at=tested_at.isoformat(),
    )


def enable_service_draft(
    session: Session,
    system_id: int,
    service_id: int,
    org_id: int,
    *,
    actor_id: str = "",
) -> ServiceOut:
    system = require_system(session, system_id, org_id)
    service = get_service_for_system(session, system.id, service_id)
    if not service:
        raise LookupError("服务不存在")
    if service.enabled:
        return _service_out(service)
    if service.probe_status != "passed" or not service.tested_at:
        raise ValueError("服务必须测试通过后才能启用")
    service.enabled = True
    session.add(service)
    record_audit_event(
        session,
        org_id=org_id,
        system_id=system.id,
        event_type="service.enabled",
        actor_type="user",
        actor_id=actor_id,
        target_type="service",
        target_id=service.id,
        output={"name": service.name, "probe_detail": service.probe_detail},
    )
    session.commit()
    session.refresh(service)
    return _service_out(service)


def delete_service(
    session: Session,
    system_id: int,
    service_id: int,
    org_id: int,
    *,
    actor_id: str = "",
) -> None:
    require_system(session, system_id, org_id)
    service = get_service_for_system(session, system_id, service_id)
    if not service:
        raise LookupError("服务不存在")
    record_audit_event(
        session,
        org_id=org_id,
        system_id=system_id,
        event_type="service.deleted",
        actor_type="user",
        actor_id=actor_id,
        target_type="service",
        target_id=service.id,
        input={"name": service.name, "enabled": service.enabled},
    )
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


def update_restart_policy(
    session: Session,
    system_id: int,
    org_id: int,
    current_user: User,
    body: RestartPolicyUpdate,
) -> RestartPolicyOut:
    system = require_system(session, system_id, org_id)
    if not can_manage_restart_policy(current_user, system):
        raise PermissionError("只有组织 owner 可以管理重启权限")

    allowed_ids = sorted({int(user_id) for user_id in body.authorized_user_ids if user_id > 0})
    if current_user.id not in allowed_ids:
        allowed_ids.insert(0, current_user.id)

    org_user_ids = {
        user_id
        for user_id in session.exec(select(User.id).where(User.org_id == org_id)).all()
    }
    invalid_ids = [user_id for user_id in allowed_ids if user_id not in org_user_ids]
    if invalid_ids:
        raise ValueError(f"用户 {invalid_ids} 不属于当前组织")

    system.restart_policy = {"authorized_user_ids": allowed_ids}
    session.add(system)
    session.commit()
    session.refresh(system)

    policy = normalize_restart_policy(system)
    return RestartPolicyOut(
        authorized_user_ids=policy["authorized_user_ids"],
        has_permission=True,
        can_manage=True,
    )

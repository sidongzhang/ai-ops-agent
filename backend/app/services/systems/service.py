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
    MonitoringConfig,
    RestartPolicyOut,
    RestartExecuteOut,
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
    resolve_registered_restart_target,
)
from app.services.realtime.websocket import manager


def require_system(session: Session, system_id: int, org_id: int) -> MonitoredSystem:
    system = get_system_for_org(session, system_id, org_id)
    if not system:
        raise LookupError("系统不存在")
    return system


def _get_primary_collector(session: Session, system_id: int) -> Collector | None:
    collectors = list(session.exec(select(Collector).where(Collector.system_id == system_id)))
    connected = [collector for collector in collectors if manager.is_connected(collector.id)]
    if connected:
        return max(
            connected,
            key=lambda item: (item.last_seen is not None, item.last_seen, item.id or 0),
        )
    return max(
        collectors,
        key=lambda item: (item.last_seen is not None, item.last_seen, item.id or 0),
        default=None,
    )


def _build_system_out(
    session: Session,
    system: MonitoredSystem,
    services: list[Service],
    current_user: User | None = None,
) -> SystemOut:
    policy = normalize_restart_policy(system)
    collector = _get_primary_collector(session, system.id)
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


def get_monitoring_config(system: MonitoredSystem) -> MonitoringConfig:
    infra = decrypt_sensitive_fields(system.infra or {})
    raw = infra.get("monitoring") or {}
    try:
        interval = int(raw.get("interval_seconds", 60) or 60)
    except (TypeError, ValueError):
        interval = 60
    return MonitoringConfig(
        enabled=bool(raw.get("enabled", True)),
        interval_seconds=max(15, min(interval, 86400)),
    )


def update_monitoring_config(
    session: Session,
    system_id: int,
    org_id: int,
    body: MonitoringConfig,
    *,
    actor_id: str = "",
) -> MonitoringConfig:
    system = require_system(session, system_id, org_id)
    infra = decrypt_sensitive_fields(system.infra or {})
    infra["monitoring"] = body.model_dump()
    system.infra = encrypt_sensitive_fields(infra)
    session.add(system)
    record_audit_event(
        session,
        org_id=org_id,
        system_id=system.id,
        event_type="monitoring.config_updated",
        actor_type="user",
        actor_id=actor_id,
        target_type="system",
        target_id=str(system.id),
        input=body.model_dump(),
    )
    session.commit()
    return body


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


async def test_service_draft(
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
    if not system.local:
        collector = _get_primary_collector(session, system.id)
        if not collector:
            raise ValueError("远程系统请先安装并连接采集器")
        if not manager.is_connected(collector.id):
            raise ValueError("采集器当前离线，请先启动采集器")
        try:
            response = await manager.send_command(
                collector.id,
                "health_check",
                {"service": service.name},
            )
        except Exception as exc:
            detail = str(exc).strip() or repr(exc)
            raise ValueError(f"远程采集器执行失败：{detail}") from exc
        items = response.get("result", []) if response.get("ok") else []
        item = next((entry for entry in items if entry.get("name") == service.name), None)
        ok = bool(response.get("ok") and item and item.get("ok"))
        detail = item.get("detail", "远程服务未返回状态") if item else str(response.get("result", "远程测试失败"))
    else:
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
    if service.probe_status != "passed":
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


async def execute_registered_service_restart(
    session: Session,
    system_id: int,
    org_id: int,
    service_name: str,
    current_user: User,
    *,
    actor_id: str = "",
) -> RestartExecuteOut:
    from app.services.workflows.execution import execute_workflow_action

    system = require_system(session, system_id, org_id)
    if not has_restart_permission(current_user, system):
        raise PermissionError("当前用户没有该系统的重启权限")

    services = list_services_for_system(session, system.id)
    descriptor = system_to_descriptor(system, [service for service in services if service.enabled])
    service = next((item for item in descriptor.get("services", []) if item.get("name") == service_name), None)
    if not service:
        raise LookupError("服务不存在，或尚未启用监控")

    action_type = "restart_systemd" if service.get("systemd_unit") else "restart_container"
    action = {
        "type": action_type,
        "service": service_name,
        "args": {
            "unit": str(service.get("systemd_unit", "") or ""),
            "container": str(service.get("container", "") or ""),
        },
    }
    target = resolve_registered_restart_target(descriptor, action)
    detail = await execute_workflow_action(
        {"system_id": system.id, "descriptor": descriptor},
        action,
    )
    record_audit_event(
        session,
        org_id=org_id,
        system_id=system.id,
        event_type="service.restart_executed",
        actor_type="user",
        actor_id=actor_id,
        target_type="service",
        target_id=service_name,
        input={"action": action},
        output={"detail": detail},
    )
    session.commit()
    return RestartExecuteOut(
        ok=True,
        service=service_name,
        target_type=target["target_type"],
        target_resource=target.get("container", "") or target.get("unit", ""),
        detail=detail,
    )


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


def get_restart_policy(
    session: Session,
    system_id: int,
    org_id: int,
    current_user: User,
) -> RestartPolicyOut:
    system = require_system(session, system_id, org_id)
    policy = normalize_restart_policy(system)
    return RestartPolicyOut(
        authorized_user_ids=policy["authorized_user_ids"],
        has_permission=has_restart_permission(current_user, system),
        can_manage=can_manage_restart_policy(current_user, system),
    )



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

"""服务注册生命周期：新增/编辑/测试/启用/删除与授权重启执行。

从 systems/service.py 拆出——服务从「草稿 → 测试通过 → 启用」的规则（后端强制测试
通过才能启用、配置变更需重测）集中在这里，便于单独理解与测试。
"""
from datetime import datetime, timezone

from sqlmodel import Session

from app.core.security import encrypt_sensitive_fields
from app.models.auth import User
from app.models.systems import MonitoredSystem, Service
from app.repositories.systems import get_service_for_system
from app.schemas import (
    RestartExecuteOut,
    ServiceIn,
    ServiceOut,
    ServiceProbeOut,
)
from app.services.audit import record_audit_event
from app.services.descriptors.builder import service_to_descriptor, system_to_descriptor
from app.services.descriptors.runtime import get_connector
from app.services.realtime.websocket import manager
from app.services.systems.access import get_primary_collector, require_system
from app.services.systems.restart import (
    has_restart_permission,
    resolve_registered_restart_target,
)
from app.services.systems.views import service_out


def probe_service(system: MonitoredSystem, service: Service) -> tuple[bool, str]:
    descriptor = system_to_descriptor(system, [])
    service_descriptor = service_to_descriptor(service)
    try:
        return get_connector(service_descriptor, descriptor).health()
    except Exception as exc:  # noqa: BLE001
        return False, f"检查失败: {exc}"


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
    return service_out(service)


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
    return service_out(service)


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
        collector = get_primary_collector(session, system.id)
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
        ok, detail = probe_service(system, service)
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
        return service_out(service)
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
    return service_out(service)


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

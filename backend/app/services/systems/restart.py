"""Restart policy and capability helpers."""
from typing import Any

from app.models.auth import User
from app.models.collectors import Collector
from app.models.systems import MonitoredSystem, Service
from app.services.realtime.websocket import manager


def build_default_restart_policy(owner_user_id: int | None = None) -> dict[str, Any]:
    policy = {"authorized_user_ids": []}
    if owner_user_id is not None:
        policy["authorized_user_ids"] = [owner_user_id]
    return policy


def normalize_restart_policy(system: MonitoredSystem) -> dict[str, Any]:
    policy = system.restart_policy or {}
    user_ids = policy.get("authorized_user_ids", [])
    normalized_ids = sorted({int(user_id) for user_id in user_ids if str(user_id).isdigit()})
    return {"authorized_user_ids": normalized_ids}


def can_manage_restart_policy(user: User, system: MonitoredSystem) -> bool:
    return user.org_id == system.org_id and user.role == "owner"


def has_restart_permission(user: User, system: MonitoredSystem) -> bool:
    if user.org_id != system.org_id:
        return False
    if user.role == "owner":
        return True
    policy = normalize_restart_policy(system)
    return user.id in policy["authorized_user_ids"]


def get_service_runtime(service: dict | Service) -> dict[str, str]:
    config = service.config if isinstance(service, Service) else service.get("config", {})
    return {
        "container": str(service.get("container", "") if isinstance(service, dict) else "") or str(config.get("container", "") or ""),
        "systemd_unit": str(service.get("systemd_unit", "") if isinstance(service, dict) else "") or str(config.get("systemd_unit", "") or ""),
        "selector": str(service.get("selector", "") if isinstance(service, dict) else "") or str(config.get("selector", "") or ""),
    }


def list_restartable_services(services: list[dict | Service]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for service in services:
        name = service.name if isinstance(service, Service) else str(service.get("name", ""))
        runtime = get_service_runtime(service)
        items.append(
            {
                "name": name,
                "container": runtime["container"],
                "restartable": bool(runtime["container"]),
            }
        )
    return items


def build_restart_capability(
    system: MonitoredSystem,
    services: list[Service],
    collector: Collector | None,
    user: User | None,
) -> dict[str, Any]:
    restartable_services = list_restartable_services(services)
    collector_online = bool(collector and manager.is_connected(collector.id))
    has_targets = any(item["restartable"] for item in restartable_services)
    execution_mode = "local" if system.local else "collector"
    enabled = has_targets and (system.local or collector_online)

    reason = ""
    if not has_targets:
        reason = "当前系统没有配置可重启的 Docker 容器"
    elif not system.local and not collector:
        reason = "该远程系统未安装采集器，无法执行重启"
    elif not system.local and not collector_online:
        reason = "采集器当前离线，无法执行远程重启"

    return {
        "enabled": enabled,
        "execution_mode": execution_mode if has_targets else "unavailable",
        "collector_online": collector_online,
        "has_permission": has_restart_permission(user, system) if user else False,
        "reason": reason,
        "services": restartable_services,
    }


def resolve_registered_restart_target(descriptor: dict, action: dict) -> dict[str, str]:
    service_name = str(action.get("service", "") or "").strip()
    container = str(action.get("args", {}).get("container", "") or "").strip()
    if not service_name:
        raise RuntimeError("未指定服务名，无法执行重启")
    if not container:
        raise RuntimeError("未指定容器名（args.container）")

    service = next(
        (item for item in descriptor.get("services", []) if item.get("name") == service_name),
        None,
    )
    if not service:
        raise RuntimeError(f"系统中不存在服务「{service_name}」")

    runtime = get_service_runtime(service)
    expected_container = runtime["container"]
    if not expected_container:
        raise RuntimeError(f"服务「{service_name}」未配置 config.container，暂不支持重启")
    if container != expected_container:
        raise RuntimeError(f"容器「{container}」与系统注册配置不一致")

    return {
        "service": service_name,
        "container": expected_container,
        "execution_mode": "local" if descriptor.get("local") else "collector",
    }


def annotate_restart_action(descriptor: dict, action: dict) -> dict[str, Any]:
    if action.get("type") != "restart_container":
        return action

    enriched = dict(action)
    enriched["args"] = dict(action.get("args", {}))
    try:
        target = resolve_registered_restart_target(descriptor, action)
        enriched["service"] = target["service"]
        enriched["args"]["container"] = target["container"]
        enriched["execution_mode"] = target["execution_mode"]
        enriched["restart_ready"] = True
        enriched["restart_blocker"] = ""
        enriched["target_resource"] = target["container"]
    except RuntimeError as exc:
        enriched["execution_mode"] = "local" if descriptor.get("local") else "collector"
        enriched["restart_ready"] = False
        enriched["restart_blocker"] = str(exc)
        enriched["target_resource"] = str(action.get("args", {}).get("container", "") or "")
    return enriched

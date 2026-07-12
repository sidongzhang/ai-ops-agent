"""Workflow action execution helpers."""
import logging
import shlex
import socket
import subprocess
import time

from sqlmodel import Session

from app.core.database import engine
from app.services.descriptors.health import collect_health, read_service_logs
from app.services.collectors.exec import select_online_collector
from app.services.realtime.websocket import manager
from app.services.systems.restart import resolve_registered_restart_target

log = logging.getLogger(__name__)

_REDIS_CMD_ALLOWLIST = {"FLUSHDB", "FLUSHALL", "CONFIG SET", "BGREWRITEAOF", "BGSAVE", "DEBUG SLEEP"}


async def execute_workflow_action(state: dict, action: dict) -> str:
    action_type = action.get("type", "manual")
    if action_type in ("fetch_logs", "health_check"):
        return await _execute_diagnostic_action(state, action_type, action)
    if action_type in ("restart_container", "restart_systemd"):
        return await _execute_restart_action(state, action)
    if action_type == "run_redis_command":
        return await _execute_redis_command(state, action)
    return _format_manual_result(action)


def verify_action_recovery(state: dict, action: dict) -> tuple[bool, str]:
    action_type = action.get("type", "manual")
    if action_type not in ("restart_container", "restart_systemd"):
        return True, "该操作无需自动回查。"

    descriptor = state.get("descriptor", {})
    service_name = action.get("service", "").strip()
    if not descriptor or not service_name:
        return False, "缺少服务信息，无法回查恢复状态。"

    results = collect_health(descriptor)
    matched = [item for item in results if item.get("name") == service_name]
    if not matched:
        return False, f"回查失败：系统中未找到服务「{service_name}」。"

    item = matched[0]
    if item.get("ok"):
        return True, f"回查通过：{service_name} 当前正常，{item.get('detail', '')}"
    return False, f"回查未恢复：{service_name} 仍然异常，{item.get('detail', '')}"


async def verify_action_recovery_async(state: dict, action: dict) -> tuple[bool, str]:
    """远程重启后的回查必须经过采集器，不能直连客户内网地址。"""
    if action.get("type") not in ("restart_container", "restart_systemd") or state.get("descriptor", {}).get("local"):
        return verify_action_recovery(state, action)

    service_name = action.get("service", "").strip()
    with Session(engine) as session:
        collector = select_online_collector(session, state["system_id"])
    if not collector:
        return False, "远程系统没有采集器，无法回查恢复状态。"
    if not manager.is_connected(collector.id):
        return False, "远程采集器已离线，无法回查恢复状态。"
    result = await manager.send_command(collector.id, "health_check", {"service": service_name})
    if not result.get("ok"):
        return False, f"远程回查失败：{_extract_command_error(result)}"
    matched = [item for item in result.get("result", []) if item.get("name") == service_name]
    if not matched:
        return False, f"回查失败：远程系统中未找到服务「{service_name}」。"
    item = matched[0]
    if item.get("ok"):
        return True, f"回查通过：{service_name} 当前正常，{item.get('detail', '')}"
    return False, f"回查未恢复：{service_name} 仍然异常，{item.get('detail', '')}"


async def _execute_diagnostic_action(state: dict, action_type: str, action: dict) -> str:
    with Session(engine) as session:
        collector = select_online_collector(session, state["system_id"])

    if not collector:
        return _execute_without_collector(state, action_type, action)
    if not manager.is_connected(collector.id):
        raise RuntimeError("采集器当前离线，请先确认采集器在线后重试")

    command_args = {"service": action.get("service", "")}
    command_args.update(action.get("args", {}))
    command_result = await manager.send_command(collector.id, action_type, command_args)

    if not command_result.get("ok"):
        raise RuntimeError(_extract_command_error(command_result))
    raw = command_result.get("result", "")
    if isinstance(raw, list):
        return "\n".join(
            f"  {item['name']}: {item['detail']} {'✅' if item['ok'] else '❌'}" for item in raw
        )
    return str(raw)


def _execute_without_collector(state: dict, action_type: str, action: dict) -> str:
    descriptor = state.get("descriptor", {})
    if not descriptor.get("local"):
        raise RuntimeError("该系统没有采集器，无法自动执行")

    if action_type == "health_check":
        service_name = action.get("service", "").strip()
        results = collect_health(descriptor)
        if service_name:
            results = [item for item in results if item.get("name") == service_name]
            if not results:
                raise RuntimeError(f"系统中不存在服务「{service_name}」")
        return "\n".join(
            f"  {item['name']}: {item['detail']} {'✅' if item['ok'] else '❌'}" for item in results
        ) or "未找到可检查的服务"

    if action_type == "fetch_logs":
        service_name = action.get("service", "").strip()
        if not service_name:
            raise RuntimeError("未指定日志服务名，无法直接拉取日志")
        lines = int(action.get("args", {}).get("lines", 100) or 100)
        return read_service_logs(descriptor, service_name, lines)

    raise RuntimeError("该系统没有采集器，无法自动执行")


async def _execute_restart_action(state: dict, action: dict) -> str:
    descriptor = state.get("descriptor", {})
    target = resolve_registered_restart_target(descriptor, action)
    if descriptor.get("local"):
        if target["target_type"] == "systemd":
            return _exec_restart_systemd(target["unit"])
        return _exec_restart_container(target["container"])

    with Session(engine) as session:
        collector = select_online_collector(session, state["system_id"])
    if not collector:
        raise RuntimeError("该系统未安装采集器，无法执行远程重启")
    if not manager.is_connected(collector.id):
        raise RuntimeError("采集器当前离线，无法执行远程重启")

    command_name = "restart_systemd" if target["target_type"] == "systemd" else "restart_container"
    command_args = {"service": target["service"]}
    command_args["unit" if target["target_type"] == "systemd" else "container"] = target[
        "unit" if target["target_type"] == "systemd" else "container"
    ]
    command_result = await manager.send_command(
        collector.id,
        command_name,
        command_args,
    )
    if not command_result.get("ok"):
        raise RuntimeError(_extract_command_error(command_result))

    payload = command_result.get("result", {})
    target_resource = payload.get("target") if isinstance(payload, dict) else target.get("container", target.get("unit", ""))
    duration_ms = payload.get("duration_ms") if isinstance(payload, dict) else None
    duration_suffix = f"\n\n耗时：{duration_ms} ms" if duration_ms is not None else ""
    label = "systemd 服务" if target["target_type"] == "systemd" else "容器"
    return f"✅ {label} `{target_resource}` 已通过远程采集器成功重启。{duration_suffix}"


async def _execute_redis_command(state: dict, action: dict) -> str:
    if state.get("descriptor", {}).get("local"):
        return _exec_redis_command(action)
    with Session(engine) as session:
        collector = select_online_collector(session, state["system_id"])
    if not collector:
        raise RuntimeError("该远程系统未安装采集器，无法执行 Redis 命令")
    if not manager.is_connected(collector.id):
        raise RuntimeError("远程采集器当前离线，无法执行 Redis 命令")
    command = action.get("args", {}).get("command", "").strip()
    result = await manager.send_command(
        collector.id,
        "run_redis_command",
        {"service": action.get("service", ""), "command": command},
    )
    if not result.get("ok"):
        raise RuntimeError(_extract_command_error(result))
    return f"✅ 已通过远程采集器执行 Redis 命令 `{command}`。\n\n服务器响应：\n{result.get('result', '')}"


def _exec_restart_container(container: str) -> str:
    started_at = time.monotonic()
    result = subprocess.run(
        ["docker", "restart", container],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"docker restart 失败，退出码 {result.returncode}")
    duration_ms = int((time.monotonic() - started_at) * 1000)
    log.info("[workflow] docker restart %s → 成功", container)
    return f"✅ 容器 `{container}` 已成功重启。\n\n耗时：{duration_ms} ms"


def _exec_restart_systemd(unit: str) -> str:
    started_at = time.monotonic()
    safe_unit = shlex.quote(unit)
    result = subprocess.run(
        ["sh", "-c", f"systemctl restart {safe_unit} && systemctl is-active {safe_unit}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    detail = (result.stdout or result.stderr).strip()
    if result.returncode != 0 or detail.splitlines()[-1:] != ["active"]:
        raise RuntimeError(detail or f"systemd 服务 {unit} 重启失败")
    duration_ms = int((time.monotonic() - started_at) * 1000)
    return f"✅ systemd 服务 `{unit}` 已成功重启。\n\n耗时：{duration_ms} ms"


def _exec_redis_command(action: dict) -> str:
    command = action.get("args", {}).get("command", "").strip()
    if not command:
        raise RuntimeError("未指定 Redis 命令（args.command）")
    first = command.upper().split()[0]
    two = " ".join(command.upper().split()[:2])
    if first not in _REDIS_CMD_ALLOWLIST and two not in _REDIS_CMD_ALLOWLIST:
        raise RuntimeError(f"Redis 命令「{command}」不在允许执行的范围内（仅限运维类命令）")
    parts = command.strip().split()
    resp = f"*{len(parts)}\r\n" + "".join(f"${len(p)}\r\n{p}\r\n" for p in parts)
    try:
        with socket.create_connection(("localhost", 6379), timeout=5) as conn:
            conn.sendall(resp.encode())
            raw = conn.recv(4096).decode("utf-8", errors="replace").strip()
    except Exception as exc:
        raise RuntimeError(f"Redis 连接失败: {exc}") from exc
    log.info("[workflow] redis command: %s → %s", command, raw)
    return f"✅ Redis 命令 `{command}` 执行成功。\n\n服务器响应：`{raw}`"


def _format_manual_result(action: dict) -> str:
    steps = action.get("manual_steps", [])
    if steps:
        return "已记录，请按以下步骤手动执行：\n" + "\n".join(
            f"  {index + 1}. {step}" for index, step in enumerate(steps)
        )
    return "已批准，请参照诊断结果手动处理。"


def _extract_command_error(command_result: dict) -> str:
    result = command_result.get("result", "执行失败")
    if isinstance(result, dict):
        return str(result.get("error") or result.get("message") or result)
    return str(result)

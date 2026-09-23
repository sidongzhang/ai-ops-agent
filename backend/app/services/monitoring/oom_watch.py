"""容器 OOM / 异常退出巡检 —— 把「Agent 才能发现的容器级故障」变成主动告警。

背景：服务探活（collect_health）只回答「端口通不通」，容器被 OOMKilled 但探活
恰好通过（或服务未注册探活地址）时，巡检不会产生告警——直到用户问起。
本模块在定时巡检里追加一步：对注册了 container 的服务执行 docker inspect，
检测 OOMKilled / 异常退出，状态变化时走统一告警管道（消息中心 + 多渠道推送）。

去重：system.last_health["oom_watch"] 记录上轮状态，仅状态变化（新 OOM / 恢复）时动作；
告警冷却沿用系统级 last_alert_at（alert_if_needed 同款机制）。
"""
import logging
import subprocess

from sqlmodel import Session

from app.models.messages import SystemMessage
from app.models.systems import MonitoredSystem
from app.services.audit import record_audit_event
from app.services.messages import create_alert_message
from app.services.notifications.alerts import (
    configured_notification_channels,
    send_alert_channel_with_retry,
)

log = logging.getLogger(__name__)


def _inspect_container(container: str) -> dict | None:
    try:
        proc = subprocess.run(
            ["docker", "inspect", container, "--format",
             "Status={{.State.Status}}|OOMKilled={{.State.OOMKilled}}|ExitCode={{.State.ExitCode}}"
             "|Restarts={{.RestartCount}}|FinishedAt={{.State.FinishedAt}}"],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            return None
        fields = dict(kv.split("=", 1) for kv in proc.stdout.strip().split("|") if "=" in kv)
        return {
            "status": fields.get("Status", "unknown"),
            "oom_killed": fields.get("OOMKilled") == "true",
            "exit_code": int(fields.get("ExitCode", "0") or 0),
            "restarts": int(fields.get("Restarts", "0") or 0),
            "finished_at": fields.get("FinishedAt", ""),
        }
    except Exception as exc:  # noqa: BLE001 - docker 不可用时静默跳过本轮
        log.debug(f"[oom-watch] inspect {container} 失败: {exc}")
        return None


def _signature(state: dict) -> str:
    """状态指纹：变化才告警/恢复（同一次故障不重复提醒）。"""
    if not state:
        return "none"
    if state.get("oom_killed"):
        return "oom"
    if state.get("status") == "exited":
        return f"exited:{state.get('exit_code')}"
    if state.get("status") == "running":
        return "running"
    return f"{state.get('status')}:{state.get('exit_code')}"


def check_container_ooms(session: Session, system: MonitoredSystem, descriptor: dict) -> None:
    """巡检附加步骤：容器级 OOM/异常退出检测。状态变化 → 告警或自动恢复。"""
    findings: dict[str, dict] = {}
    for svc in descriptor.get("services", []):
        container = svc.get("container")
        if not container:
            continue
        state = _inspect_container(container)
        if state:
            findings[svc.get("name", container)] = state

    if not findings:
        return

    last_health = system.last_health or {}
    prev: dict = last_health.get("oom_watch", {})

    # 新发故障：本次 OOM/exited 且上一轮不是同状态
    new_oom = [
        name for name, st in findings.items()
        if _signature(st) in ("oom", f"exited:{st.get('exit_code')}") and prev.get(name) != _signature(st)
    ]
    # 恢复：上次是故障态、本次 running
    recovered = [
        name for name, st in findings.items()
        if _signature(st) == "running" and _signature(prev.get(name, {}) or {}) != "running"
    ]

    system.last_health = {**last_health, "oom_watch": findings}
    session.add(system)

    if new_oom:
        _alert_oom(session, system, findings, new_oom)
    if recovered:
        _resolve_oom_alerts(session, system, recovered, findings)


def _oom_remedy(service: str, state: dict) -> str:
    if state.get("oom_killed"):
        return (
            f"容器 {service} 因内存超限被内核 OOM 杀死（ExitCode=137）。"
            "建议：调高容器 mem_limit、下调应用内存占用（如 innodb_buffer_pool_size），"
            "并排查宿主机内存占用大户。"
        )
    return (
        f"容器以 ExitCode={state.get('exit_code')} 异常退出。"
        "建议查看服务日志定位退出原因，确认是否配置了 restart 策略。"
    )


def _alert_oom(session: Session, system: MonitoredSystem, findings: dict, new_oom: list[str]) -> None:
    services = "、".join(new_oom)
    detail = "\n".join(
        f"- {name}: OOMKilled={'true' if findings[name]['oom_killed'] else 'false'}, "
        f"ExitCode={findings[name]['exit_code']}, 状态={findings[name]['status']}"
        for name in new_oom
    )
    message = SystemMessage(
        org_id=system.org_id,
        system_id=system.id,
        message_type="alert",
        severity="error",
        title=f"系统「{system.name}」检测到容器异常退出",
        summary=f"容器级故障：{services}（OOMKilled / 非零退出）",
        content=f"平台容器巡检发现以下服务容器异常：\n" + "\n".join(
            f"{name}: {_signature(findings[name])}" for name in new_oom
        ),
        diagnosis=(
            "容器已被系统强杀或自行退出。" if findings[new_oom[0]].get("oom_killed")
            else f"容器 ExitCode={findings[new_oom[0]]['exit_code']} 退出。"
        ),
        suggestion="\n".join(_oom_remedy(name, findings[name]) for name in new_oom),
        source="container_oom_watch",
        related={"oom_services": new_oom, "findings": {k: v for k, v in findings.items() if k in new_oom}},
    )
    session.add(message)
    session.flush()
    from app.services.incidents.service import attach_message_to_incident
    attach_message_to_incident(session, system, message, new_oom)

    notify = system.notify or {}
    channels = configured_notification_channels(notify)
    channel_results = [{"type": "web", "status": "success", "attempts": 1}]
    if channels:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max(1, len(channels))) as executor:
            futures = [
                executor.submit(
                    send_alert_channel_with_retry,
                    channel,
                    notify,
                    system.name,
                    new_oom,
                )
                for channel in channels
            ]
            channel_results.extend(f.result() for f in futures)
    message.channels = channel_results
    system.last_alert_at = system.last_alert_at or None  # 告警时间由系统级冷却统一管理

    record_audit_event(
        session,
        org_id=system.org_id,
        system_id=system.id,
        event_type="message.oom_alert",
        actor_type="system",
        actor_id="oom-watch",
        target_type="message",
        target_id=message.id,
        output={"oom_services": new_oom, "channels": channels},
    )
    session.flush()
    log.warning(f"[oom-watch] 系统「{system.name}」容器异常: {new_oom}")


def _resolve_oom_alerts(session: Session, system: MonitoredSystem, recovered: list[str], findings: dict) -> None:
    from sqlmodel import select

    active = session.exec(
        select(SystemMessage).where(
            SystemMessage.system_id == system.id,
            SystemMessage.org_id == system.org_id,
            SystemMessage.message_type == "alert",
            SystemMessage.status != "resolved",
            SystemMessage.source == "container_oom_watch",
        )
    ).all()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    recovered_set = set(recovered)
    for message in active:
        oom = set((message.related or {}).get("oom_services") or [])
        if not oom or not oom.issubset(recovered_set):
            continue
        message.status = "resolved"
        message.read_at = message.read_at or now
        message.resolved_at = now
        message.summary = f"已恢复：{'、'.join(sorted(oom))}（容器 running）"
        session.add(message)
        session.flush()
        log.info(f"[oom-watch] 系统「{system.name}」容器已恢复: {oom}")

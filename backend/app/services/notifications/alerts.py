"""Alert delivery and cooldown logic."""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
from sqlmodel import Session, select

from app.core.config import settings
from app.core.security import decrypt_sensitive_fields
from app.models.messages import SystemMessage
from app.models.systems import MonitoredSystem
from app.services.audit import record_audit_event
from app.services.messages import create_alert_message
from .email import send_email
from .feishu import FeishuAlerter

log = logging.getLogger(__name__)

SUPPORTED_CHANNELS = ("feishu", "email", "webhook")
MAX_DELIVERY_ATTEMPTS = 3


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def newly_failed_services(old_health: dict, new_results: list[dict]) -> list[str]:
    old_map = {service["name"]: service.get("ok", True) for service in old_health.get("services", [])}
    return [
        result["name"]
        for result in new_results
        if not result["ok"] and old_map.get(result["name"], True)
    ]


def recovered_services(old_health: dict, new_results: list[dict]) -> list[str]:
    old_map = {service["name"]: service.get("ok", True) for service in old_health.get("services", [])}
    return [
        result["name"]
        for result in new_results
        if result.get("ok") and old_map.get(result["name"], True) is False
    ]


def resolve_recovered_alerts(system: MonitoredSystem, recovered: list[str], session: Session) -> None:
    if not recovered:
        return
    recovered_set = set(recovered)
    active = session.exec(
        select(SystemMessage).where(
            SystemMessage.system_id == system.id,
            SystemMessage.org_id == system.org_id,
            SystemMessage.message_type == "alert",
            SystemMessage.status != "resolved",
        )
    ).all()
    now = utcnow()
    for message in active:
        failed = set((message.related or {}).get("failed_services") or [])
        if not failed or not failed.issubset(recovered_set):
            continue
        message.status = "resolved"
        message.read_at = message.read_at or now
        message.ack_at = message.ack_at or now
        message.resolved_at = now
        message.summary = f"已恢复：{'、'.join(sorted(failed))}"
        message.content = f"系统「{system.name}」异常服务已恢复：{'、'.join(sorted(failed))}。"
        session.add(message)
        record_audit_event(
            session,
            org_id=system.org_id,
            system_id=system.id,
            event_type="message.auto_resolved",
            actor_type="system",
            actor_id="health-check",
            target_type="message",
            target_id=message.id,
            output={"recovered_services": sorted(failed)},
        )


def send_webhook_alert(url: str, system_name: str, failed_services: list[str]) -> dict:
    lines = [f"🚨 系统「{system_name}」告警：以下服务异常"]
    lines.extend(f"  ✗ {service}" for service in failed_services)
    payload = {"msg_type": "text", "content": {"text": "\n".join(lines)}}
    try:
        response = httpx.post(url, json=payload, timeout=8)
        log.info(f"[alert] webhook → {url} status={response.status_code}")
        if response.status_code >= 400:
            return {
                "type": "webhook",
                "status": "failed",
                "detail": f"HTTP {response.status_code}",
                "retryable": response.status_code == 429 or response.status_code >= 500,
            }
        return {"type": "webhook", "status": "success"}
    except Exception as exc:
        log.warning(f"[alert] webhook 发送失败 {url}: {exc}")
        return {"type": "webhook", "status": "failed", "detail": str(exc), "retryable": True}


def send_feishu_alert(cfg: dict, system_name: str, failed_services: list[str]) -> dict:
    decrypted = decrypt_sensitive_fields(cfg)
    app_id = decrypted.get("app_id", "")
    app_secret = decrypted.get("app_secret", "")
    chat_id = decrypted.get("chat_id", "")
    if not all([app_id, app_secret, chat_id]):
        log.warning(f"[alert][feishu] 系统「{system_name}」飞书配置不完整，跳过")
        return {"type": "feishu", "status": "failed", "detail": "飞书配置不完整", "retryable": False}
    try:
        FeishuAlerter(app_id, app_secret).send_alert(chat_id, system_name, failed_services)
        return {"type": "feishu", "status": "success"}
    except Exception as exc:
        log.warning(f"[alert][feishu] 发送失败: {exc}")
        return {"type": "feishu", "status": "failed", "detail": str(exc), "retryable": True}


def send_email_alert(cfg: dict, system_name: str, failed_services: list[str]) -> dict:
    decrypted = decrypt_sensitive_fields(cfg)
    services = "、".join(failed_services)
    body = (
        f"系统「{system_name}」告警：以下服务异常\n\n"
        f"{services}\n\n"
        "建议：请进入 AIOps 平台查看消息中心中的诊断建议，并在系统详情页重新探活确认。"
    )
    try:
        send_email(decrypted, f"[AIOps] 系统「{system_name}」服务异常", body)
        return {"type": "email", "status": "success"}
    except ValueError as exc:
        log.warning(f"[alert][email] 配置错误: {exc}")
        return {"type": "email", "status": "failed", "detail": str(exc), "retryable": False}
    except Exception as exc:
        log.warning(f"[alert][email] 发送失败: {exc}")
        return {"type": "email", "status": "failed", "detail": str(exc), "retryable": True}


def configured_notification_channels(cfg: dict) -> list[str]:
    """Return configured external channels while preserving legacy type configs."""
    configured = cfg.get("channels") or []
    if not configured:
        legacy_type = cfg.get("type", "none")
        configured = [] if legacy_type == "none" else [legacy_type]
    return [channel for channel in SUPPORTED_CHANNELS if channel in configured]


def send_alert_channel(
    channel: str,
    cfg: dict,
    system_name: str,
    failed_services: list[str],
) -> dict:
    if channel == "feishu":
        return send_feishu_alert(cfg, system_name, failed_services)
    if channel == "email":
        return send_email_alert(cfg, system_name, failed_services)
    if channel == "webhook":
        url = decrypt_sensitive_fields(cfg).get("webhook_url", "")
        if not url:
            return {
                "type": "webhook",
                "status": "failed",
                "detail": "Webhook 配置不完整",
                "retryable": False,
            }
        return send_webhook_alert(url, system_name, failed_services)
    return {
        "type": channel,
        "status": "failed",
        "detail": "不支持的通知渠道",
        "retryable": False,
    }


def send_alert_channel_with_retry(
    channel: str,
    cfg: dict,
    system_name: str,
    failed_services: list[str],
    *,
    max_attempts: int = MAX_DELIVERY_ATTEMPTS,
) -> dict:
    """Retry transient delivery failures and return one durable channel result."""
    result = {"type": channel, "status": "failed", "detail": "发送失败"}
    attempts = 0
    for attempts in range(1, max(1, max_attempts) + 1):
        result = send_alert_channel(channel, cfg, system_name, failed_services)
        if result.get("status") == "success":
            break
        if result.get("retryable") is False:
            break
    return {
        **result,
        "attempts": attempts,
        "last_attempt_at": utcnow().isoformat(),
    }


def alert_if_needed(
    system: MonitoredSystem,
    new_results: list[dict],
    session: Session,
) -> None:
    resolve_recovered_alerts(system, recovered_services(system.last_health or {}, new_results), session)
    failed = newly_failed_services(system.last_health or {}, new_results)
    if not failed:
        return

    now = utcnow()
    if system.last_alert_at:
        elapsed = (now - system.last_alert_at.replace(tzinfo=timezone.utc)).total_seconds()
        if elapsed < settings.alert_cooldown_seconds:
            log.info(
                f"[alert] 系统「{system.name}」冷却期内（{elapsed:.0f}s < "
                f"{settings.alert_cooldown_seconds}s），跳过告警"
            )
            return

    log.warning(f"[alert] 系统「{system.name}」新异常服务: {failed}")
    message = create_alert_message(session, system, failed)

    notify = system.notify or {}
    channels = configured_notification_channels(notify)
    channel_results = [{
        "type": "web",
        "status": "success",
        "attempts": 1,
        "last_attempt_at": now.isoformat(),
    }]

    if channels:
        with ThreadPoolExecutor(max_workers=len(channels)) as executor:
            futures = [
                executor.submit(
                    send_alert_channel_with_retry,
                    channel,
                    notify,
                    system.name,
                    failed,
                )
                for channel in channels
            ]
            channel_results.extend(future.result() for future in futures)
    if not channels:
        log.info(f"[alert] 系统「{system.name}」未配置通知渠道，仅记录日志")

    message.channels = channel_results
    failed_channels = [
        result["type"]
        for result in channel_results
        if result.get("type") != "web" and result.get("status") == "failed"
    ]
    record_audit_event(
        session,
        org_id=system.org_id,
        system_id=system.id,
        event_type="notification.delivered",
        actor_type="system",
        actor_id="health-check",
        target_type="message",
        target_id=message.id,
        status="failed" if failed_channels else "success",
        input={"failed_services": failed, "channels": channels},
        output={"channel_results": channel_results, "failed_channels": failed_channels},
    )
    system.last_alert_at = now
    session.add(system)
    session.add(message)

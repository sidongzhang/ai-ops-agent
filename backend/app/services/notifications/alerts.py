"""Alert delivery and cooldown logic."""
import logging
from datetime import datetime, timezone

import httpx
from sqlmodel import Session

from app.core.config import settings
from app.core.security import decrypt_sensitive_fields
from app.models.systems import MonitoredSystem
from .feishu import FeishuAlerter

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def newly_failed_services(old_health: dict, new_results: list[dict]) -> list[str]:
    old_map = {service["name"]: service.get("ok", True) for service in old_health.get("services", [])}
    return [
        result["name"]
        for result in new_results
        if not result["ok"] and old_map.get(result["name"], True)
    ]


def send_webhook_alert(url: str, system_name: str, failed_services: list[str]) -> None:
    lines = [f"🚨 系统「{system_name}」告警：以下服务异常"]
    lines.extend(f"  ✗ {service}" for service in failed_services)
    payload = {"msg_type": "text", "content": {"text": "\n".join(lines)}}
    try:
        response = httpx.post(url, json=payload, timeout=8)
        log.info(f"[alert] webhook → {url} status={response.status_code}")
    except Exception as exc:
        log.warning(f"[alert] webhook 发送失败 {url}: {exc}")


def send_feishu_alert(cfg: dict, system_name: str, failed_services: list[str]) -> None:
    decrypted = decrypt_sensitive_fields(cfg)
    app_id = decrypted.get("app_id", "")
    app_secret = decrypted.get("app_secret", "")
    chat_id = decrypted.get("chat_id", "")
    if not all([app_id, app_secret, chat_id]):
        log.warning(f"[alert][feishu] 系统「{system_name}」飞书配置不完整，跳过")
        return
    try:
        FeishuAlerter(app_id, app_secret).send_alert(chat_id, system_name, failed_services)
    except Exception as exc:
        log.warning(f"[alert][feishu] 发送失败: {exc}")


def alert_if_needed(
    system: MonitoredSystem,
    new_results: list[dict],
    session: Session,
) -> None:
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

    notify = system.notify or {}
    notify_type = notify.get("type", "none")

    if notify_type == "feishu":
        send_feishu_alert(notify, system.name, failed)
    elif notify_type == "webhook" or notify.get("webhook_url"):
        url = notify.get("webhook_url", "")
        if url:
            send_webhook_alert(url, system.name, failed)
    else:
        log.info(f"[alert] 系统「{system.name}」未配置通知渠道，仅记录日志")

    system.last_alert_at = now
    session.add(system)

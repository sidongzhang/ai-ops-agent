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
from app.services.notifications.deliveries import record_notification_delivery
from .email import send_email
from .feishu import FeishuAlerter

log = logging.getLogger(__name__)

SUPPORTED_CHANNELS = ("feishu", "email", "webhook")
MAX_DELIVERY_ATTEMPTS = 3


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _open_alert_messages(system: MonitoredSystem, session: Session) -> list[SystemMessage]:
    return list(session.exec(
        select(SystemMessage).where(
            SystemMessage.system_id == system.id,
            SystemMessage.org_id == system.org_id,
            SystemMessage.message_type == "alert",
            SystemMessage.status != "resolved",
        )
    ))


def reconcile_alerts(system: MonitoredSystem, new_results: list[dict], session: Session) -> list[str]:
    """按**当前状态**对齐告警，返回「当前异常且没有任何未解决告警覆盖」的服务名。

    为什么不用「上一轮→本轮」的跃迁判断：`system.last_health` 有多个写入方
    （采集器上报、定时巡检、外部系统上报、容器 OOM 巡检），任一写入方都会吞掉跃迁，
    导致告警**永久挂起**（服务早已恢复但告警不关）或反向**故障静默**（告警被误关后不再开）。
    这里只依赖本轮结果这一权威事实：
      * 未解决告警里的服务若当前全部正常 → 关闭
      * 当前异常但未被任何未解决告警覆盖 → 交给调用方新建（受冷却约束）
    """
    if not new_results:
        return []
    ok_map = {str(result.get("name")): bool(result.get("ok", True)) for result in new_results}
    now = utcnow()
    covered: set[str] = set()
    for message in _open_alert_messages(system, session):
        failed = set((message.related or {}).get("failed_services") or [])
        if not failed:
            continue
        # 服务已不在本轮结果里 = 已禁用/删除/不再监控 → 视为恢复（否则告警永久挂起）
        def _still_broken(name: str) -> bool:
            return name in ok_map and not ok_map[name]

        if not any(_still_broken(name) for name in failed):
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
        else:
            covered |= {name for name in failed if name in ok_map}
    return sorted(name for name, ok in ok_map.items() if not ok and name not in covered)


def recently_alerted_services(
    system: MonitoredSystem,
    session: Session,
    *,
    within_seconds: int,
) -> set[str]:
    """冷却窗口内已经被告警过的服务名。

    用于抑制**同一服务**的抖动重复告警；不影响其他服务的新故障——
    系统级冷却会把无关服务的新故障一起压掉，造成「故障静默」。
    """
    from datetime import timedelta

    since = utcnow() - timedelta(seconds=max(0, within_seconds))
    rows = session.exec(
        select(SystemMessage).where(
            SystemMessage.system_id == system.id,
            SystemMessage.org_id == system.org_id,
            SystemMessage.message_type == "alert",
            SystemMessage.created_at >= since,
        )
    ).all()
    names: set[str] = set()
    for message in rows:
        names |= set((message.related or {}).get("failed_services") or [])
    return names


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


def build_alert_delivery_snapshot(
    channel: str,
    cfg: dict,
    system_name: str,
    failed_services: list[str],
) -> dict:
    decrypted = decrypt_sensitive_fields(cfg)
    services = "、".join(failed_services)
    title = f"[AIOps] 系统「{system_name}」服务异常"
    text = (
        f"系统「{system_name}」告警：以下服务异常\n\n"
        f"{services}\n\n"
        "建议：请进入 AIOps 平台查看消息中心中的诊断建议，并在系统详情页重新探活确认。"
    )
    if channel == "feishu":
        return {
            "recipient": decrypted.get("chat_id", ""),
            "subject": f"系统「{system_name}」服务告警",
            "body": "\n".join(["以下服务出现异常：", *(f"✗ {service}" for service in failed_services)]),
            "payload": {"msg_type": "interactive", "failed_services": failed_services},
        }
    if channel == "email":
        return {
            "recipient": decrypted.get("email_to", ""),
            "subject": title,
            "body": text,
            "payload": {"failed_services": failed_services},
        }
    if channel == "webhook":
        return {
            "recipient": decrypted.get("webhook_url", ""),
            "subject": f"系统「{system_name}」服务告警",
            "body": text,
            "payload": {"msg_type": "text", "failed_services": failed_services},
        }
    return {"recipient": "", "subject": title, "body": text, "payload": {"failed_services": failed_services}}


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


def _message_notification_text(system_name: str, message: SystemMessage) -> str:
    suggestions = "\n".join(f"- {item}" for item in (message.suggestion or [])[:5])
    parts = [
        f"系统「{system_name}」消息：{message.title}",
        "",
        message.summary or message.content,
    ]
    if message.diagnosis:
        parts.extend(["", "原因分析：", message.diagnosis])
    if suggestions:
        parts.extend(["", "处理建议：", suggestions])
    return "\n".join(part for part in parts if part is not None)


def send_message_channel(channel: str, cfg: dict, system_name: str, message: SystemMessage) -> dict:
    decrypted = decrypt_sensitive_fields(cfg)
    text = _message_notification_text(system_name, message)
    try:
        if channel == "feishu":
            app_id = decrypted.get("app_id", "")
            app_secret = decrypted.get("app_secret", "")
            chat_id = decrypted.get("chat_id", "")
            if not all([app_id, app_secret, chat_id]):
                return {"type": "feishu", "status": "failed", "detail": "飞书配置不完整", "retryable": False}
            result = FeishuAlerter(app_id, app_secret).send_text(chat_id, text)
            if result.get("code") not in (None, 0):
                return {"type": "feishu", "status": "failed", "detail": str(result), "retryable": True}
            return {"type": "feishu", "status": "success", "provider_message_id": result.get("data", {}).get("message_id", "")}
        if channel == "email":
            send_email(decrypted, f"[AIOps] {message.title}", text)
            return {"type": "email", "status": "success"}
        if channel == "webhook":
            url = decrypted.get("webhook_url", "")
            if not url:
                return {"type": "webhook", "status": "failed", "detail": "Webhook 配置不完整", "retryable": False}
            response = httpx.post(url, json={"msg_type": "text", "content": {"text": text}}, timeout=8)
            if response.status_code >= 400:
                return {
                    "type": "webhook",
                    "status": "failed",
                    "detail": f"HTTP {response.status_code}",
                    "retryable": response.status_code == 429 or response.status_code >= 500,
                }
            return {"type": "webhook", "status": "success"}
        return {"type": channel, "status": "failed", "detail": "不支持的通知渠道", "retryable": False}
    except ValueError as exc:
        return {"type": channel, "status": "failed", "detail": str(exc), "retryable": False}
    except Exception as exc:
        return {"type": channel, "status": "failed", "detail": str(exc), "retryable": True}


def send_message_channel_with_retry(
    channel: str,
    cfg: dict,
    system_name: str,
    message: SystemMessage,
    *,
    max_attempts: int = MAX_DELIVERY_ATTEMPTS,
) -> dict:
    result = {"type": channel, "status": "failed", "detail": "发送失败"}
    attempts = 0
    for attempts in range(1, max(1, max_attempts) + 1):
        result = send_message_channel(channel, cfg, system_name, message)
        if result.get("status") == "success":
            break
        if result.get("retryable") is False:
            break
    return {**result, "attempts": attempts, "last_attempt_at": utcnow().isoformat()}


def deliver_message_notifications(
    session: Session,
    system: MonitoredSystem,
    message: SystemMessage,
    *,
    actor_id: str = "openapi",
    force: bool = False,
) -> SystemMessage:
    related = dict(message.related or {})
    if related.get("notification_sent_at") and not force:
        return message

    notify = system.notify or {}
    channels = configured_notification_channels(notify)
    now = utcnow()
    channel_results = [{
        "type": "web",
        "status": "success",
        "attempts": 1,
        "last_attempt_at": now.isoformat(),
    }]
    if channels:
        with ThreadPoolExecutor(max_workers=len(channels)) as executor:
            futures = [
                executor.submit(send_message_channel_with_retry, channel, notify, system.name, message)
                for channel in channels
            ]
            channel_results.extend(future.result() for future in futures)

    text = _message_notification_text(system.name, message)
    decrypted = decrypt_sensitive_fields(notify)
    for result in channel_results:
        channel = result.get("type", "")
        recipient = ""
        if channel == "feishu":
            recipient = decrypted.get("chat_id", "")
        elif channel == "email":
            recipient = decrypted.get("email_to", "")
        elif channel == "webhook":
            recipient = decrypted.get("webhook_url", "")
        record_notification_delivery(
            session,
            message,
            channel=channel,
            recipient=recipient,
            subject=f"[AIOps] {message.title}",
            body=text,
            payload={"message_type": message.message_type, "request_id": related.get("request_id")},
            result=result,
        )

    failed_channels = [
        result["type"]
        for result in channel_results
        if result.get("type") != "web" and result.get("status") == "failed"
    ]
    related["notification_sent_at"] = now.isoformat()
    message.related = related
    message.channels = channel_results
    session.add(message)
    record_audit_event(
        session,
        org_id=system.org_id,
        system_id=system.id,
        event_type="notification.delivered",
        actor_type="system_token",
        actor_id=actor_id,
        target_type="message",
        target_id=message.id,
        status="failed" if failed_channels else "success",
        input={"message_type": message.message_type, "channels": channels},
        output={"channel_results": channel_results, "failed_channels": failed_channels},
    )
    session.commit()
    session.refresh(message)
    return message


def alert_if_needed(
    system: MonitoredSystem,
    new_results: list[dict],
    session: Session,
) -> None:
    # 先按当前状态对齐：关闭已恢复的告警，拿到「异常但未被告警覆盖」的服务
    failed = reconcile_alerts(system, new_results, session)
    if not failed:
        return

    now = utcnow()
    # 抖动抑制：只压掉冷却窗口内**同一服务**的重复告警；
    # 其他服务的新故障必须立刻告警（系统级冷却会造成跨服务静默）。
    recent = recently_alerted_services(
        system, session, within_seconds=settings.alert_cooldown_seconds
    )
    suppressed = [name for name in failed if name in recent]
    failed = [name for name in failed if name not in recent]
    if suppressed:
        log.info(f"[alert] 系统「{system.name}」冷却期内抑制重复告警: {suppressed}")
    if not failed:
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
    snapshots = {
        channel: build_alert_delivery_snapshot(channel, notify, system.name, failed)
        for channel in channels
    }
    for result in channel_results:
        channel = result.get("type", "")
        snapshot = snapshots.get(channel, {})
        record_notification_delivery(
            session,
            message,
            channel=channel,
            recipient=snapshot.get("recipient", ""),
            subject=snapshot.get("subject", message.title),
            body=snapshot.get("body", message.content),
            payload=snapshot.get("payload", {}),
            result=result,
        )
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

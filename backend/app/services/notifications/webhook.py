"""Feishu webhook handling helpers."""
import json
import logging
import threading
from collections import deque

from fastapi import BackgroundTasks
from sqlmodel import Session, select

from app.core.config import settings
from app.core.database import engine
from app.core.security import decrypt_sensitive_fields
from app.models.systems import MonitoredSystem, Service
from app.services.descriptors.builder import system_to_descriptor
from .feishu import FeishuClient

log = logging.getLogger(__name__)

_seen: deque = deque(maxlen=200)
_seen_lock = threading.Lock()


def already_seen(event_id: str) -> bool:
    if not event_id:
        return False
    with _seen_lock:
        if event_id in _seen:
            return True
        _seen.append(event_id)
        return False


def get_feishu_client(app_id: str = "", app_secret: str = "") -> FeishuClient:
    return FeishuClient(
        app_id=app_id or settings.feishu_app_id,
        app_secret=app_secret or settings.feishu_app_secret,
    )


def find_systems_by_app_id(app_id: str) -> list[dict]:
    result = []
    with Session(engine) as session:
        systems = session.exec(select(MonitoredSystem)).all()
        for system in systems:
            notify = system.notify or {}
            if notify.get("app_id") == app_id:
                services = list(session.exec(select(Service).where(Service.system_id == system.id)))
                result.append(system_to_descriptor(system, services))
    return result


def resolve_app_secret(app_id: str) -> str:
    with Session(engine) as session:
        for system in session.exec(select(MonitoredSystem)).all():
            notify = system.notify or {}
            if notify.get("app_id") == app_id:
                decrypted = decrypt_sensitive_fields(notify)
                return decrypted.get("app_secret", settings.feishu_app_secret)
    return settings.feishu_app_secret


def handle_message(text: str, chat_id: str, message_id: str, app_id: str, app_secret: str) -> None:
    from app.agent.diagnostics.runner import diagnose

    feishu = get_feishu_client(app_id, app_secret)
    reaction_id = ""
    try:
        reaction_id = feishu.add_reaction(message_id, "OK")
        descriptors = find_systems_by_app_id(app_id or settings.feishu_app_id)
        if not descriptors:
            feishu.send_text(chat_id, "⚠️ 当前没有系统绑定到这个机器人，请先在平台注册系统并配置飞书通知。")
            return

        descriptor = descriptors[0]
        log.info(f"[feishu-webhook] 诊断系统「{descriptor['name']}」问题: {text[:80]}")
        answer = diagnose(descriptor, text)
        feishu.delete_reaction(message_id, reaction_id)
        reaction_id = ""
        feishu.send_card(chat_id, answer)
    except Exception as exc:
        log.error(f"[feishu-webhook] 处理消息出错: {exc}", exc_info=True)
        if reaction_id:
            feishu.delete_reaction(message_id, reaction_id)
        feishu.send_text(chat_id, f"❌ 处理出错：{exc}")


async def process_webhook_payload(body: dict, background: BackgroundTasks) -> dict:
    challenge = body.get("challenge") or body.get("event", {}).get("challenge")
    if challenge:
        token = body.get("token", "")
        if settings.feishu_verification_token and token and token != settings.feishu_verification_token:
            log.warning("[feishu-webhook] verification token 不匹配")
            return {"error": "invalid token"}
        log.info("[feishu-webhook] URL 验证成功")
        return {"challenge": challenge}

    header = body.get("header", {})
    event_type = header.get("event_type", "") or body.get("type", "")
    if not event_type:
        return {"code": 0}

    event_id = header.get("event_id", "")
    if already_seen(event_id):
        return {"code": 0}

    if event_type == "im.message.receive_v1":
        event = body.get("event", {})
        message = event.get("message", {})
        if message.get("message_type") == "text":
            raw = json.loads(message.get("content", "{}"))
            text = " ".join(word for word in raw.get("text", "").strip().split() if not word.startswith("@")).strip()
            chat_id = message.get("chat_id", "") or settings.feishu_alert_chat_id
            message_id = message.get("message_id", "")
            if text and chat_id:
                app_id = header.get("app_id", settings.feishu_app_id)
                app_secret = resolve_app_secret(app_id)
                background.add_task(handle_message, text, chat_id, message_id, app_id, app_secret)
    return {"code": 0}

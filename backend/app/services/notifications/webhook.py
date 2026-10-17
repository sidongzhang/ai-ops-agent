"""Feishu webhook handling helpers."""
import base64
import hashlib
import hmac
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


class WebhookAuthError(Exception):
    """Webhook 验签失败。status_code 供 API 层直接映射 HTTP 状态。"""

    def __init__(self, message: str, status_code: int = 403) -> None:
        self.status_code = status_code
        super().__init__(message)


def decrypt_feishu_payload(encrypted: str, encrypt_key: str) -> dict:
    """按飞书事件加密规范解密：AES-256-CBC，key=sha256(Encrypt Key)，IV=密文前 16 字节。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    try:
        raw = base64.b64decode(encrypted)
    except Exception as exc:  # noqa: BLE001
        raise WebhookAuthError("加密事件不是合法的 base64", 400) from exc
    if len(raw) <= 16:
        raise WebhookAuthError("加密事件长度非法", 400)

    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv, ciphertext = raw[:16], raw[16:]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()

    pad = padded[-1] if padded else 0
    if pad < 1 or pad > 16 or padded[-pad:] != bytes([pad]) * pad:
        raise WebhookAuthError("加密事件解密失败（padding 非法）", 400)
    try:
        return json.loads(padded[:-pad].decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise WebhookAuthError("加密事件解密后不是合法 JSON", 400) from exc


def _verification_token_of(payload: dict) -> str:
    """兼容飞书 v2（header.token）与 v1（顶层 token）两种事件格式。"""
    header = payload.get("header") or {}
    return str(header.get("token") or payload.get("token") or "")


def verify_webhook(body: dict) -> dict:
    """校验来源并返回（可能的）解密后明文事件。

    设计取舍：未配置 Verification Token 时**拒绝处理**而不是放行——
    这个端点可以用租户自己的凭据驱动一次带工具的诊断，匿名可达等于把平台变成代理。
    """
    payload = body
    if "encrypt" in body:
        if not settings.feishu_encrypt_key:
            raise WebhookAuthError("收到加密事件，但服务端未配置 FEISHU_ENCRYPT_KEY", 503)
        payload = decrypt_feishu_payload(str(body["encrypt"]), settings.feishu_encrypt_key)

    expected = settings.feishu_verification_token
    if not expected:
        raise WebhookAuthError("服务端未配置 FEISHU_VERIFICATION_TOKEN，拒绝处理", 503)

    if not hmac.compare_digest(_verification_token_of(payload), expected):
        raise WebhookAuthError("verification token 不匹配", 403)
    return payload


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
    from app.agent.runtime.entrypoint import diagnose

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
    payload = verify_webhook(body)

    challenge = payload.get("challenge") or payload.get("event", {}).get("challenge")
    if challenge:
        log.info("[feishu-webhook] URL 验证成功")
        return {"challenge": challenge}

    header = payload.get("header", {})
    event_type = header.get("event_type", "") or payload.get("type", "")
    if not event_type:
        return {"code": 0}

    event_id = header.get("event_id", "")
    if already_seen(event_id):
        return {"code": 0}

    if event_type == "im.message.receive_v1":
        event = payload.get("event", {})
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

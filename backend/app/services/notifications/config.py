"""Notification configuration and delivery logic."""
from collections.abc import Callable

import httpx

from app.schemas import NotifyConfig
from app.services.notifications.email import send_email
from app.services.notifications.feishu import FeishuAlerter

SUPPORTED_CHANNELS = ("feishu", "email", "webhook")


def merge_notify_config(body: NotifyConfig, existing_notify: dict | None) -> dict:
    raw = body.model_dump()
    old_notify = existing_notify or {}
    if raw.get("app_secret") == "***" and old_notify.get("app_secret"):
        raw["app_secret"] = old_notify["app_secret"]
    if raw.get("smtp_password") == "***" and old_notify.get("smtp_password"):
        raw["smtp_password"] = old_notify["smtp_password"]
    raw["channels"] = [channel for channel in SUPPORTED_CHANNELS if channel in raw.get("channels", [])]
    if not raw["channels"] and raw.get("type") in SUPPORTED_CHANNELS:
        raw["channels"] = [raw["type"]]
    raw["type"] = raw["channels"][0] if raw["channels"] else "none"
    return raw


def configured_test_channels(cfg: dict) -> list[str]:
    channels = cfg.get("channels") or []
    if not channels and cfg.get("type") in SUPPORTED_CHANNELS:
        channels = [cfg["type"]]
    return [channel for channel in SUPPORTED_CHANNELS if channel in channels]


def send_test_notification(
    system_name: str,
    cfg: dict,
    feishu_client_cls: type[FeishuAlerter] = FeishuAlerter,
    webhook_post: Callable[..., httpx.Response] = httpx.post,
    email_sender: Callable[..., None] = send_email,
) -> list[dict]:
    channels = configured_test_channels(cfg)
    if not channels:
        raise ValueError("当前未配置任何通知渠道")
    results = []

    if "feishu" in channels:
        app_id = cfg.get("app_id", "")
        app_secret = cfg.get("app_secret", "")
        chat_id = cfg.get("chat_id", "")
        if not all([app_id, app_secret, chat_id]):
            raise ValueError("飞书配置不完整（app_id / app_secret / chat_id）")
        feishu_client_cls(app_id, app_secret).send_test(chat_id, system_name)
        results.append({"type": "feishu", "status": "success"})

    if "webhook" in channels:
        url = cfg.get("webhook_url", "")
        if not url:
            raise ValueError("未配置 webhook_url")
        resp = webhook_post(
            url,
            json={
                "msg_type": "text",
                "content": {"text": f"✅ AIOps 通知测试：系统「{system_name}」配置成功"},
            },
            timeout=8,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Webhook 返回 {resp.status_code}")
        results.append({"type": "webhook", "status": "success"})

    if "email" in channels:
        email_sender(
            cfg,
            f"AIOps 通知测试：{system_name}",
            f"AIOps 通知测试：系统「{system_name}」邮件配置成功。",
        )
        results.append({"type": "email", "status": "success"})
    return results

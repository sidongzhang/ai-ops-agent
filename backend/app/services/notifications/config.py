"""Notification configuration and delivery logic."""
from collections.abc import Callable

import httpx

from app.schemas import NotifyConfig
from app.services.notifications.feishu import FeishuAlerter


def merge_notify_config(body: NotifyConfig, existing_notify: dict | None) -> dict:
    raw = body.model_dump()
    old_notify = existing_notify or {}
    if raw.get("app_secret") == "***" and old_notify.get("app_secret"):
        raw["app_secret"] = old_notify["app_secret"]
    return raw


def send_test_notification(
    system_name: str,
    cfg: dict,
    feishu_client_cls: type[FeishuAlerter] = FeishuAlerter,
    webhook_post: Callable[..., httpx.Response] = httpx.post,
) -> None:
    notify_type = cfg.get("type", "none")

    if notify_type == "feishu":
        app_id = cfg.get("app_id", "")
        app_secret = cfg.get("app_secret", "")
        chat_id = cfg.get("chat_id", "")
        if not all([app_id, app_secret, chat_id]):
            raise ValueError("飞书配置不完整（app_id / app_secret / chat_id）")
        feishu_client_cls(app_id, app_secret).send_test(chat_id, system_name)
        return

    if notify_type == "webhook":
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
        return

    raise ValueError("当前未配置任何通知渠道（type=none）")

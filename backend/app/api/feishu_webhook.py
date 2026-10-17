"""Feishu webhook endpoint."""
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from ..services.notifications.webhook import WebhookAuthError, process_webhook_payload

router = APIRouter(prefix="/feishu", tags=["feishu-webhook"])


@router.post("/webhook")
async def feishu_webhook(request: Request, background: BackgroundTasks):
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "请求体不是合法 JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")

    try:
        return await process_webhook_payload(body, background)
    except WebhookAuthError as exc:
        # 验签失败统一返回非 2xx，飞书会重试并暴露配置问题，便于发现配置错误。
        raise HTTPException(exc.status_code, str(exc)) from exc

"""Feishu webhook endpoint."""
from fastapi import APIRouter, BackgroundTasks, Request

from ..services.notifications.webhook import process_webhook_payload

router = APIRouter(prefix="/feishu", tags=["feishu-webhook"])

@router.post("/webhook")
async def feishu_webhook(request: Request, background: BackgroundTasks):
    body = await request.json()
    return await process_webhook_payload(body, background)

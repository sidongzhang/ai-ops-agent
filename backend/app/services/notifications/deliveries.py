"""Durable notification delivery history."""
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.models.messages import SystemMessage
from app.models.notifications import NotificationDelivery


def record_notification_delivery(
    session: Session,
    message: SystemMessage,
    *,
    channel: str,
    recipient: str = "",
    subject: str = "",
    body: str = "",
    payload: dict | None = None,
    result: dict | None = None,
) -> NotificationDelivery:
    result = result or {}
    delivery = NotificationDelivery(
        org_id=message.org_id,
        system_id=message.system_id,
        message_id=message.id,
        channel=channel,
        recipient=recipient,
        subject=subject,
        body=body,
        payload=payload or {},
        provider_response=result,
        provider_message_id=str(result.get("provider_message_id") or result.get("message_id") or ""),
        status=result.get("status", "pending"),
        attempts=int(result.get("attempts") or 0),
        failed_reason=str(result.get("detail") or ""),
        sent_at=datetime.now(timezone.utc),
    )
    session.add(delivery)
    return delivery


def list_message_deliveries(
    session: Session,
    *,
    message_id: int,
    org_id: int,
) -> list[NotificationDelivery]:
    return list(session.exec(
        select(NotificationDelivery)
        .where(
            NotificationDelivery.message_id == message_id,
            NotificationDelivery.org_id == org_id,
        )
        .order_by(NotificationDelivery.created_at.desc())
    ))

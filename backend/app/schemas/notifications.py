"""Schemas for notification delivery history."""
from datetime import datetime

from pydantic import BaseModel, Field


class NotificationDeliveryOut(BaseModel):
    id: int
    org_id: int
    system_id: int
    message_id: int
    channel: str
    recipient: str = ""
    subject: str = ""
    body: str = ""
    payload: dict = Field(default_factory=dict)
    provider_response: dict = Field(default_factory=dict)
    provider_message_id: str = ""
    status: str
    attempts: int = 0
    failed_reason: str = ""
    sent_at: datetime | None = None
    created_at: datetime

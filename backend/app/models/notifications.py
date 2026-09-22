"""Notification delivery history models."""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .common import utcnow


class NotificationDelivery(SQLModel, table=True):
    __tablename__ = "notification_deliveries"

    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="orgs.id", index=True)
    system_id: int = Field(foreign_key="systems.id", index=True)
    message_id: int = Field(foreign_key="system_messages.id", index=True)
    channel: str = Field(index=True)
    recipient: str = ""
    subject: str = ""
    body: str = ""
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    provider_response: dict = Field(default_factory=dict, sa_column=Column(JSON))
    provider_message_id: str = ""
    status: str = Field(default="pending", index=True)
    attempts: int = 0
    failed_reason: str = ""
    sent_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)

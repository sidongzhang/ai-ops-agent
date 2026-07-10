"""Schemas for the system message center."""
from datetime import datetime

from pydantic import BaseModel, Field


class SystemMessageOut(BaseModel):
    id: int
    org_id: int
    system_id: int
    message_type: str
    severity: str
    title: str
    summary: str = ""
    content: str = ""
    diagnosis: str = ""
    suggestion: list[str] = Field(default_factory=list)
    status: str
    source: str = "platform"
    related: dict = Field(default_factory=dict)
    channels: list[dict] = Field(default_factory=list)
    created_at: datetime
    read_at: datetime | None = None
    ack_at: datetime | None = None
    resolved_at: datetime | None = None


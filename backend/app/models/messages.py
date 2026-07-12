"""System message center models."""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .common import utcnow


class SystemMessage(SQLModel, table=True):
    __tablename__ = "system_messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="orgs.id", index=True)
    system_id: int = Field(foreign_key="systems.id", index=True)
    incident_id: Optional[int] = Field(default=None, foreign_key="incidents.id", index=True)
    message_type: str = Field(default="alert", index=True)
    severity: str = Field(default="warning", index=True)
    title: str
    summary: str = ""
    content: str = ""
    diagnosis: str = ""
    suggestion: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    status: str = Field(default="unread", index=True)
    source: str = Field(default="platform")
    related: dict = Field(default_factory=dict, sa_column=Column(JSON))
    channels: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow, index=True)
    read_at: Optional[datetime] = Field(default=None)
    ack_at: Optional[datetime] = Field(default=None)
    resolved_at: Optional[datetime] = Field(default=None)

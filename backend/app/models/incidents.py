"""Incident grouping for related alerts."""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .common import utcnow


class Incident(SQLModel, table=True):
    __tablename__ = "incidents"

    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="orgs.id", index=True)
    system_id: int = Field(foreign_key="systems.id", index=True)
    title: str
    status: str = Field(default="open", index=True)
    severity: str = Field(default="warning", index=True)
    failed_services: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    message_count: int = Field(default=1)
    first_seen: datetime = Field(default_factory=utcnow, index=True)
    last_seen: datetime = Field(default_factory=utcnow, index=True)
    resolved_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, index=True)

"""Incident API schemas."""
from datetime import datetime

from pydantic import BaseModel, Field

from .messages import SystemMessageOut


class IncidentOut(BaseModel):
    id: int
    org_id: int
    system_id: int
    system_name: str = ""
    title: str
    status: str
    severity: str
    failed_services: list[str] = Field(default_factory=list)
    message_count: int = 0
    first_seen: datetime
    last_seen: datetime
    resolved_at: datetime | None = None
    created_at: datetime


class IncidentSummaryOut(BaseModel):
    total: int = 0
    open: int = 0
    acknowledged: int = 0
    resolved: int = 0


class IncidentDetailOut(IncidentOut):
    messages: list[SystemMessageOut] = Field(default_factory=list)

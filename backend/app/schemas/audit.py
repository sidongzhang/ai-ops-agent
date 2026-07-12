"""Schemas for audit logs."""
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.pagination import PageOut


class AuditLogOut(BaseModel):
    id: int
    org_id: int
    system_id: int | None = None
    actor_type: str
    actor_id: str
    event_type: str
    target_type: str = ""
    target_id: str = ""
    status: str
    input: dict = Field(default_factory=dict)
    output: dict = Field(default_factory=dict)
    created_at: datetime


class AuditLogPageOut(PageOut[AuditLogOut]):
    pass

"""Audit log models."""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .common import utcnow


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="orgs.id", index=True)
    system_id: Optional[int] = Field(default=None, foreign_key="systems.id", index=True)
    actor_type: str = Field(default="platform", index=True)
    actor_id: str = Field(default="", index=True)
    event_type: str = Field(index=True)
    target_type: str = ""
    target_id: str = ""
    status: str = Field(default="success", index=True)
    input: dict = Field(default_factory=dict, sa_column=Column(JSON))
    output: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow, index=True)

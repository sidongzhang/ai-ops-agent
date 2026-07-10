"""Approval workflow models."""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .common import utcnow


class ActionWorkflow(SQLModel, table=True):
    __tablename__ = "action_workflows"
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="orgs.id", index=True)
    system_id: int = Field(foreign_key="systems.id", index=True)
    requested_by_user_id: Optional[int] = Field(default=None, foreign_key="users.id", index=True)
    approved_by_user_id: Optional[int] = Field(default=None, foreign_key="users.id", index=True)
    thread_id: str = Field(index=True)
    question: str
    diagnosis: str = Field(default="")
    proposed_action: dict = Field(default_factory=dict, sa_column=Column(JSON))
    target_service: str = Field(default="")
    target_resource: str = Field(default="")
    execution_mode: str = Field(default="")
    status: str = Field(default="pending", index=True)
    execution_result: str = Field(default="")
    approved_at: Optional[datetime] = Field(default=None)
    executed_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

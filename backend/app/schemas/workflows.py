"""Workflow schemas."""
from datetime import datetime

from pydantic import BaseModel, Field


class WorkflowStart(BaseModel):
    question: str
    context: dict = Field(default_factory=dict)


class WorkflowOut(BaseModel):
    id: int
    thread_id: str
    status: str
    question: str
    diagnosis: str
    proposed_action: dict = Field(default_factory=dict)
    requested_by_user_id: int | None = None
    approved_by_user_id: int | None = None
    target_service: str = ""
    target_resource: str = ""
    execution_mode: str = ""
    execution_result: str
    approved_at: datetime | None = None
    executed_at: datetime | None = None
    created_at: datetime


class WorkflowDecision(BaseModel):
    approved: bool
    reason: str = ""

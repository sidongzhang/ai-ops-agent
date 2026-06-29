"""Workflow schemas."""
from datetime import datetime

from pydantic import BaseModel, Field


class WorkflowStart(BaseModel):
    question: str


class WorkflowOut(BaseModel):
    id: int
    thread_id: str
    status: str
    question: str
    diagnosis: str
    proposed_action: dict = Field(default_factory=dict)
    execution_result: str
    created_at: datetime


class WorkflowDecision(BaseModel):
    approved: bool
    reason: str = ""

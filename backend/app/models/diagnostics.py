"""Persisted AI diagnosis and data-analysis reports."""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .common import utcnow


class DiagnosisReport(SQLModel, table=True):
    __tablename__ = "diagnosis_reports"

    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="orgs.id", index=True)
    system_id: int = Field(foreign_key="systems.id", index=True)
    user_id: Optional[int] = Field(default=None, foreign_key="users.id", index=True)
    external_request_id: str = Field(default="", index=True, max_length=255)
    report_type: str = Field(default="diagnose", index=True)
    status: str = Field(default="success", index=True)
    question: str
    answer: str = ""
    template_name: str = ""
    template_description: str = ""
    model: str = ""
    duration_ms: int = 0
    total_tokens: int = 0
    evidence_sources: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    evidence_steps: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    tool_calls: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    evidence: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    knowledge_refs: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    business_context: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error_message: str = ""
    created_at: datetime = Field(default_factory=utcnow, index=True)

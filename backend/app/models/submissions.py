"""Durable submission identity, independent of alert request_id and chat history."""
from datetime import datetime
from sqlmodel import SQLModel, Field
from .common import utcnow


class DiagnosisSubmission(SQLModel, table=True):
    __tablename__ = "diagnosis_submission"
    id: str = Field(primary_key=True, max_length=64)
    org_id: int = Field(index=True)
    system_id: int = Field(index=True)
    fingerprint: str = Field(max_length=64)
    report_id: int | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow)

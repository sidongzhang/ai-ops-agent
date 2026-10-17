"""Transactional diagnosis outbox; Celery messages contain only the report ID."""
from datetime import datetime
from sqlalchemy import Column, JSON
from sqlmodel import SQLModel, Field
from .common import utcnow


class DiagnosisJob(SQLModel, table=True):
    __tablename__ = "diagnosis_job"
    report_id: int = Field(primary_key=True)
    state: str = Field(default="pending", index=True)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    publish_after: datetime = Field(default_factory=utcnow, index=True)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempts: int = 0
    last_error: str = ""

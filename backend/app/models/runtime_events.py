"""Idempotent persisted runtime events for report-owned runs."""
from datetime import datetime
from sqlalchemy import UniqueConstraint
from sqlmodel import SQLModel, Field
from sqlalchemy import Column, JSON
from .common import utcnow


class DiagnosisRuntimeEvent(SQLModel, table=True):
    __tablename__ = 'diagnosis_runtime_event'
    __table_args__ = (UniqueConstraint('run_id','sequence',name='uq_runtime_event_sequence'),)
    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(max_length=100,index=True)
    sequence: int
    org_id: int = Field(index=True)
    system_id: int = Field(index=True)
    kind: str = Field(max_length=64)
    payload: dict = Field(default_factory=dict,sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)

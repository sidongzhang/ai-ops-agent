"""Optional telemetry; report ownership remains the authorization boundary."""
from datetime import datetime
from sqlalchemy import Column, JSON, ForeignKey, Integer
from sqlmodel import SQLModel, Field

class DiagnosisPerformance(SQLModel, table=True):
    __tablename__ = 'diagnosis_performance'
    report_id: int = Field(sa_column=Column(Integer, ForeignKey('diagnosis_reports.id', ondelete='CASCADE'), primary_key=True))
    summary: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    detail: list | None = Field(default=None, sa_column=Column(JSON(none_as_null=True)))
    expires_at: datetime = Field(index=True)

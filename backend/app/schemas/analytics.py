"""Schemas for operational efficiency analytics."""
from datetime import date, datetime

from pydantic import BaseModel, Field


class EfficiencyTrendPoint(BaseModel):
    date: date
    alerts: int = 0
    resolved: int = 0
    diagnoses: int = 0


class SystemEfficiencyRow(BaseModel):
    system_id: int
    system_name: str
    alerts: int = 0
    unresolved: int = 0
    resolved_rate_pct: float = 0
    avg_resolution_minutes: float | None = None


class EfficiencyAnalyticsOut(BaseModel):
    days: int
    since: datetime
    alerts_total: int = 0
    alerts_resolved: int = 0
    alerts_unresolved: int = 0
    alert_resolution_rate_pct: float = 0
    avg_ack_minutes: float | None = None
    avg_resolution_minutes: float | None = None
    diagnoses_total: int = 0
    diagnosis_success_rate_pct: float = 0
    avg_diagnosis_seconds: float | None = None
    diagnosis_evidence_rate_pct: float = 0
    notification_deliveries: int = 0
    notification_failures: int = 0
    notification_success_rate_pct: float = 0
    notification_retries: int = 0
    duplicate_requests_blocked: int = 0
    duplicate_request_rate_pct: float = 0
    invalid_configs_blocked: int = 0
    workflow_executions: int = 0
    workflow_success_rate_pct: float = 0
    trend: list[EfficiencyTrendPoint] = Field(default_factory=list)
    systems: list[SystemEfficiencyRow] = Field(default_factory=list)

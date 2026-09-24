"""Schemas for the system message center."""
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.pagination import PageOut


class SystemMessageOut(BaseModel):
    id: int
    org_id: int
    system_id: int
    incident_id: int | None = None
    message_type: str
    severity: str
    title: str
    summary: str = ""
    content: str = ""
    diagnosis: str = ""
    suggestion: list[str] = Field(default_factory=list)
    status: str
    source: str = "platform"
    related: dict = Field(default_factory=dict)
    channels: list[dict] = Field(default_factory=list)
    created_at: datetime
    read_at: datetime | None = None
    ack_at: datetime | None = None
    resolved_at: datetime | None = None

    @field_validator("suggestion", mode="before")
    @classmethod
    def normalize_suggestion(cls, value):
        # Older/manual producers may have persisted a single string; keep the
        # message center resilient while new writes always use list[str].
        if value is None:
            return []
        if isinstance(value, str):
            return [line for line in value.splitlines() if line.strip()] or [value]
        return value


class SystemMessagePageOut(PageOut[SystemMessageOut]):
    pass

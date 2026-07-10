"""Schemas for external system OpenAPI."""
from pydantic import BaseModel, Field

from .health import HealthItem


class OpenAlertIn(BaseModel):
    request_id: str
    title: str
    summary: str = ""
    content: str = ""
    severity: str = "warning"
    need_llm_process: bool = False
    context: dict = Field(default_factory=dict)


class OpenHealthIn(BaseModel):
    request_id: str = ""
    services: list[HealthItem]


class OpenMessageIn(BaseModel):
    request_id: str
    title: str
    summary: str = ""
    content: str = ""
    severity: str = "info"
    need_llm_process: bool = False
    context: dict = Field(default_factory=dict)

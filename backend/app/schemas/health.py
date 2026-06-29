"""Health and monitoring schemas."""
from pydantic import BaseModel


class HealthItem(BaseModel):
    name: str
    ok: bool
    detail: str
    connector: str = ""


class SystemHealth(BaseModel):
    system_id: int
    name: str
    healthy: bool
    services: list[HealthItem]
    source: str = "direct"
    reported_at: str | None = None

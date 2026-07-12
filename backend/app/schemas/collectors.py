"""Collector schemas."""
from pydantic import BaseModel, Field

from .health import HealthItem


class CollectorCreate(BaseModel):
    name: str


class CollectorCreated(BaseModel):
    id: int
    name: str
    system_id: int
    collector_key: str


class CollectorBundleRequest(BaseModel):
    collector_key: str


class CollectorOut(BaseModel):
    id: int
    name: str
    system_id: int
    last_seen: str | None = None
    online: bool = False


class CollectorConfig(BaseModel):
    system_id: int
    name: str
    local: bool
    infra: dict = Field(default_factory=dict)
    services: list[dict] = Field(default_factory=list)


class CollectorReport(BaseModel):
    services: list[HealthItem]


class CollectorExecRequest(BaseModel):
    cmd: str
    args: dict = Field(default_factory=dict)
    collector_id: int | None = None


class CollectorExecResponse(BaseModel):
    ok: bool
    result: object
    collector_id: int

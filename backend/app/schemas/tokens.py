"""Schemas for external system tokens."""
from datetime import datetime

from pydantic import BaseModel, Field


class SystemTokenCreate(BaseModel):
    name: str
    scopes: list[str] = Field(default_factory=lambda: [
        "alert:create",
        "health:push",
        "message:read",
        "message:send",
        "report:submit",
    ])
    allowed_ips: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None


class SystemTokenCreated(BaseModel):
    id: int
    name: str
    system_id: int
    token: str
    scopes: list[str]
    allowed_ips: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None


class SystemTokenOut(BaseModel):
    id: int
    name: str
    system_id: int
    scopes: list[str]
    allowed_ips: list[str] = Field(default_factory=list)
    status: str
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime

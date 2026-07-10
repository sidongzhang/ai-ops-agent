"""External system access tokens."""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .common import utcnow


class SystemToken(SQLModel, table=True):
    __tablename__ = "system_tokens"

    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="orgs.id", index=True)
    system_id: int = Field(foreign_key="systems.id", index=True)
    name: str
    token_hash: str = Field(index=True)
    scopes: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    allowed_ips: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    status: str = Field(default="active", index=True)
    expires_at: Optional[datetime] = Field(default=None)
    last_used_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)

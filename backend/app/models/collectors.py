"""Collector connectivity models."""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from .common import utcnow


class Collector(SQLModel, table=True):
    __tablename__ = "collectors"
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="orgs.id", index=True)
    system_id: int = Field(foreign_key="systems.id", index=True)
    name: str
    token_hash: str = Field(index=True)
    last_seen: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)

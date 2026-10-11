"""变更事件：记录发布/配置/重启等变更，用于「是不是刚发版导致的」时间窗关联。"""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .common import utcnow


class ChangeEvent(SQLModel, table=True):
    __tablename__ = "change_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="orgs.id", index=True)
    system_id: int = Field(foreign_key="systems.id", index=True)
    service: str = Field(default="", max_length=128)
    kind: str = Field(default="change", max_length=32)   # deploy/config/restart/scale/change
    title: str = Field(default="", max_length=256)
    description: str = Field(default="")
    actor: str = Field(default="", max_length=128)
    external_id: str = Field(default="", max_length=128)
    details: dict = Field(default_factory=dict, sa_column=Column(JSON))
    occurred_at: datetime = Field(default_factory=utcnow, index=True)
    created_at: datetime = Field(default_factory=utcnow)

"""变更事件（change_events）的请求/响应模型。"""
from datetime import datetime

from pydantic import BaseModel, Field


class ChangeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    service: str = Field(default="", max_length=128)
    kind: str = Field(default="change", max_length=32)
    description: str = Field(default="", max_length=4000)
    actor: str = Field(default="", max_length=128)
    external_id: str = Field(default="", max_length=128)
    occurred_at: datetime | None = None
    details: dict = Field(default_factory=dict)


class ChangeOut(BaseModel):
    id: int
    system_id: int
    service: str
    kind: str
    title: str
    description: str
    actor: str
    external_id: str
    occurred_at: datetime
    created_at: datetime

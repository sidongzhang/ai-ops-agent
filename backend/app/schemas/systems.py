"""Schemas for monitored systems."""
from pydantic import BaseModel, Field


class ServiceIn(BaseModel):
    name: str
    connector: str = "http"
    config: dict = Field(default_factory=dict)


class NotifyConfig(BaseModel):
    type: str = "none"
    app_id: str = ""
    app_secret: str = ""
    chat_id: str = ""
    webhook_url: str = ""


class SystemCreate(BaseModel):
    key: str
    name: str
    local: bool = False
    notify: dict = Field(default_factory=dict)
    infra: dict = Field(default_factory=dict)
    services: list[ServiceIn] = Field(default_factory=list)


class ServiceOut(BaseModel):
    id: int
    name: str
    connector: str
    config: dict


class SystemOut(BaseModel):
    id: int
    org_id: int
    key: str
    name: str
    local: bool
    notify: dict
    infra: dict
    services: list[ServiceOut] = Field(default_factory=list)

"""Schemas for monitored systems."""
from datetime import datetime

from pydantic import BaseModel, Field


class ServiceIn(BaseModel):
    name: str
    connector: str = "http"
    config: dict = Field(default_factory=dict)


class NotifyConfig(BaseModel):
    type: str = "none"
    channels: list[str] = Field(default_factory=list)
    app_id: str = ""
    app_secret: str = ""
    chat_id: str = ""
    webhook_url: str = ""
    email_to: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: bool = True


class MonitoringConfig(BaseModel):
    enabled: bool = True
    interval_seconds: int = Field(default=60, ge=15, le=86400)


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
    enabled: bool = True
    probe_status: str = "passed"
    probe_detail: str = ""
    tested_at: datetime | None = None


class ServiceProbeOut(BaseModel):
    service_id: int
    name: str
    connector: str
    ok: bool
    detail: str
    tested_at: str


class RestartPolicyUpdate(BaseModel):
    authorized_user_ids: list[int] = Field(default_factory=list)


class RestartPolicyOut(BaseModel):
    authorized_user_ids: list[int] = Field(default_factory=list)
    has_permission: bool = False
    can_manage: bool = False


class RestartServiceOut(BaseModel):
    name: str
    container: str = ""
    systemd_unit: str = ""
    target_type: str = ""
    restartable: bool = False


class RestartCapabilityOut(BaseModel):
    enabled: bool = False
    execution_mode: str = "unavailable"
    collector_online: bool = False
    has_permission: bool = False
    reason: str = ""
    services: list[RestartServiceOut] = Field(default_factory=list)


class RestartExecuteIn(BaseModel):
    service: str


class RestartExecuteOut(BaseModel):
    ok: bool = True
    service: str
    target_type: str
    target_resource: str
    detail: str


class SystemOut(BaseModel):
    id: int
    org_id: int
    key: str
    name: str
    local: bool
    notify: dict
    infra: dict
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    restart_policy: RestartPolicyOut = Field(default_factory=RestartPolicyOut)
    restart_capability: RestartCapabilityOut = Field(default_factory=RestartCapabilityOut)
    services: list[ServiceOut] = Field(default_factory=list)

"""Schemas for monitored systems."""
from datetime import datetime
import re

from pydantic import BaseModel, Field, model_validator


_EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
_SUPPORTED_NOTIFY_CHANNELS = {"feishu", "email", "webhook"}


def _split_emails(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，;\s]+", value or "") if item.strip()]


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

    @model_validator(mode="after")
    def validate_selected_channels(self):
        channels = [channel for channel in (self.channels or []) if channel]
        if not channels and self.type in _SUPPORTED_NOTIFY_CHANNELS:
            channels = [self.type]
        unsupported = [channel for channel in channels if channel not in _SUPPORTED_NOTIFY_CHANNELS]
        if unsupported:
            raise ValueError(f"不支持的通知渠道：{', '.join(unsupported)}")

        if "feishu" in channels:
            missing = [
                label
                for label, value in (
                    ("App ID", self.app_id),
                    ("App Secret", self.app_secret),
                    ("群聊 Chat ID", self.chat_id),
                )
                if not str(value or "").strip()
            ]
            if missing:
                raise ValueError(f"飞书配置不完整：{', '.join(missing)}")

        if "webhook" in channels:
            webhook_url = self.webhook_url.strip()
            if not webhook_url:
                raise ValueError("Webhook 配置不完整：Webhook URL")
            if not webhook_url.startswith(("http://", "https://")):
                raise ValueError("Webhook URL 必须以 http:// 或 https:// 开头")

        if "email" in channels:
            recipients = _split_emails(self.email_to)
            invalid = [email for email in recipients if not _EMAIL_RE.match(email)]
            if not recipients:
                raise ValueError("邮件配置不完整：告警收件邮箱")
            if invalid:
                raise ValueError(f"收件邮箱格式不正确：{', '.join(invalid)}")
            missing = [
                label
                for label, value in (
                    ("SMTP 主机", self.smtp_host),
                    ("用户名", self.smtp_username),
                    ("发件人", self.smtp_from),
                    ("SMTP 密码", self.smtp_password),
                )
                if not str(value or "").strip()
            ]
            if missing:
                raise ValueError(f"邮件配置不完整：{', '.join(missing)}")
            if not (1 <= int(self.smtp_port or 0) <= 65535):
                raise ValueError("SMTP 端口必须在 1 到 65535 之间")
            if self.smtp_from and not _EMAIL_RE.match(self.smtp_from.strip()):
                raise ValueError("发件人邮箱格式不正确")
        return self


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

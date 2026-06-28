"""API 请求/响应模型（Pydantic）。与 DB 模型分离，控制对外暴露的字段。"""
from typing import Optional

from pydantic import BaseModel, EmailStr


# ── 鉴权 ──
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    org_name: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    email: EmailStr
    org_id: int
    role: str


# ── 系统注册 ──
class ServiceIn(BaseModel):
    name: str
    connector: str = "http"
    config: dict = {}


class SystemCreate(BaseModel):
    key: str
    name: str
    local: bool = False
    notify: dict = {}
    infra: dict = {}
    services: list[ServiceIn] = []


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
    services: list[ServiceOut] = []


# ── 监控 / 诊断 ──
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
    source: str = "direct"          # direct=平台直连探测；collector=采集器上报
    reported_at: Optional[str] = None


# ── 采集器 ──
class CollectorCreate(BaseModel):
    name: str


class CollectorCreated(BaseModel):
    id: int
    name: str
    system_id: int
    collector_key: str              # 原文密钥，仅创建时返回一次


class CollectorOut(BaseModel):
    id: int
    name: str
    system_id: int
    last_seen: Optional[str] = None


class CollectorConfig(BaseModel):
    """下发给采集器：它该探测哪些服务。"""
    system_id: int
    name: str
    local: bool
    infra: dict = {}
    services: list[dict] = []


class CollectorReport(BaseModel):
    services: list[HealthItem]


class DiagnoseRequest(BaseModel):
    question: str


class DiagnoseResponse(BaseModel):
    system_id: int
    answer: str

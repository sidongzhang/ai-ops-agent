"""
多租户数据模型（SQLModel）。
租户隔离采用行级方案：每张业务表带 org_id 外键，所有查询经依赖注入按当前用户的 org 过滤。
表名显式复数化，避开 Postgres 保留字（user）。
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Org(SQLModel, table=True):
    __tablename__ = "orgs"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=_utcnow)


class User(SQLModel, table=True):
    __tablename__ = "users"
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    hashed_password: str
    org_id: int = Field(foreign_key="orgs.id", index=True)
    role: str = Field(default="owner")
    created_at: datetime = Field(default_factory=_utcnow)


class MonitoredSystem(SQLModel, table=True):
    """客户注册进来的一套被监控系统（属于某个 org）。"""
    __tablename__ = "systems"
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="orgs.id", index=True)
    key: str = Field(index=True)                 # org 内唯一 slug
    name: str
    local: bool = Field(default=False)           # 是否平台托管（可写动作）
    notify: dict = Field(default_factory=dict, sa_column=Column(JSON))   # {type, chat_id, ...}
    infra: dict = Field(default_factory=dict, sa_column=Column(JSON))    # {mysql:{}, prometheus:{}, ...}
    # Collector 上报的最近一次健康快照（私有内网系统由采集器出站推送）
    last_health: dict = Field(default_factory=dict, sa_column=Column(JSON))
    last_report_at: Optional[datetime] = Field(default=None)
    last_alert_at: Optional[datetime] = Field(default=None)   # 上次告警时间，用于冷却期去重
    created_at: datetime = Field(default_factory=_utcnow)


class Collector(SQLModel, table=True):
    """可下载采集器：装在客户网络内，出站连平台、本地探测并上报。绑定到一套系统。"""
    __tablename__ = "collectors"
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="orgs.id", index=True)
    system_id: int = Field(foreign_key="systems.id", index=True)
    name: str
    token_hash: str = Field(index=True)          # 采集器密钥的 sha256（原文只在创建时返回一次）
    last_seen: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class Service(SQLModel, table=True):
    """被监控系统下的一个服务。config 直接对应连接器描述符字段（health_url/host/port/kind...）。"""
    __tablename__ = "services"
    id: Optional[int] = Field(default=None, primary_key=True)
    system_id: int = Field(foreign_key="systems.id", index=True)
    name: str
    connector: str = Field(default="http")       # local|http|tcp|ssh|prometheus|k8s
    config: dict = Field(default_factory=dict, sa_column=Column(JSON))

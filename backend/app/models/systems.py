"""System and service inventory models."""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .common import utcnow


class MonitoredSystem(SQLModel, table=True):
    __tablename__ = "systems"
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="orgs.id", index=True)
    key: str = Field(index=True)
    name: str
    local: bool = Field(default=False)
    notify: dict = Field(default_factory=dict, sa_column=Column(JSON))
    infra: dict = Field(default_factory=dict, sa_column=Column(JSON))
    restart_policy: dict = Field(default_factory=dict, sa_column=Column(JSON))
    last_health: dict = Field(default_factory=dict, sa_column=Column(JSON))
    last_report_at: Optional[datetime] = Field(default=None)
    last_alert_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)


class Service(SQLModel, table=True):
    __tablename__ = "services"
    id: Optional[int] = Field(default=None, primary_key=True)
    system_id: int = Field(foreign_key="systems.id", index=True)
    name: str
    connector: str = Field(default="http")
    config: dict = Field(default_factory=dict, sa_column=Column(JSON))
    enabled: bool = Field(default=True, index=True)
    probe_status: str = Field(default="passed", index=True)
    probe_detail: str = ""
    tested_at: Optional[datetime] = None

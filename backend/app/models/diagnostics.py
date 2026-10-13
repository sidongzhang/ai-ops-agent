"""Persisted AI diagnosis and data-analysis reports."""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .common import utcnow


class DiagnosisReport(SQLModel, table=True):
    __tablename__ = "diagnosis_reports"

    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="orgs.id", index=True)
    system_id: int = Field(foreign_key="systems.id", index=True)
    user_id: Optional[int] = Field(default=None, foreign_key="users.id", index=True)
    external_request_id: str = Field(default="", index=True, max_length=255)
    report_type: str = Field(default="diagnose", index=True)
    status: str = Field(default="success", index=True)
    question: str
    answer: str = ""
    template_name: str = ""
    template_description: str = ""
    model: str = ""
    duration_ms: int = 0
    total_tokens: int = 0
    evidence_sources: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    evidence_steps: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    tool_calls: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    evidence: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    knowledge_refs: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    business_context: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error_message: str = ""
    # 中断（stopping 是**派生**状态：cancel_requested_at 非空且 status == "running"，
    # 不写进 status 字段，保证旧前端/旧外部链路"status 仍是 running 就继续轮询"的语义不变）
    cancel_requested_at: Optional[datetime] = Field(default=None)
    cancelled_at: Optional[datetime] = Field(default=None)
    # 取消时的执行相位：model_request / tool_exec / idle（用于判定用量完整性与 gap 原因）
    cancel_phase: str = Field(default="", max_length=16)
    # 运行心跳：reaper 据此判断"是否还有活着的 worker 在推进"，**不再只看创建时间**
    runner_id: str = Field(default="", max_length=64)
    runner_heartbeat_at: Optional[datetime] = Field(default=None)
    # 用量完整性：有 usage ≠ 用量完整（取消落在模型请求在途时该请求用量不可得）
    usage_complete: bool = Field(default=True)
    usage_gap_reason: str = Field(default="", max_length=64)
    cost_usd_estimated: Optional[float] = Field(default=None)
    price_table_version: str = Field(default="", max_length=64)
    # 用户反馈：""（未评）/ "up" / "down"。差评会生成自改进候选。
    feedback: str = Field(default="", index=True, max_length=16)
    feedback_note: str = Field(default="", max_length=2000)
    feedback_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, index=True)

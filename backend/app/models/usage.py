"""用量账本：每次 run 一行**增量**，会话累计 = SUM(delta)。

为什么必须分增量：resume 时按官方要求传 `usage=prior`，框架返回的是**累计值**。
若把框架累计值当增量再累加一遍，就会重复计数（这是最容易出的记账 bug）。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from .common import utcnow


class DiagnosisUsageLedger(SQLModel, table=True):
    __tablename__ = "diagnosis_usage_ledger"

    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(index=True)
    system_id: int = Field(index=True)
    report_id: int = Field(index=True)
    conversation_id: str = Field(default="", max_length=64, index=True)
    # initial / resume / fallback_model / text_fallback
    run_kind: str = Field(default="initial", max_length=24)

    tokens_input: int = Field(default=0)
    tokens_output: int = Field(default=0)
    tokens_cache_read: int = Field(default=0)
    tokens_cache_write: int = Field(default=0)
    # 本次新增（= 框架累计 after − 框架累计 before）；会话累计只认它
    tokens_delta: int = Field(default=0)
    cost_usd_delta: float = Field(default=0.0)

    usage_complete: bool = Field(default=True)
    # 含保守估算（预算结算用；展示上要标"含估算"）
    estimated_portion: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow, index=True)


class DiagnosisBudgetLedger(SQLModel, table=True):
    """会话预算账本：按滚动窗口原子预留 / 结算，覆盖并发请求。

    判定用：`reserved + settled <= 预算`；并发时先原子加预留，再在同一事务里检查、超限回滚。
    """

    __tablename__ = "diagnosis_budget_ledger"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", "system_id", "window_started_at", name="uq_budget_window"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(index=True)
    user_id: int = Field(index=True)
    system_id: int = Field(index=True)
    window_started_at: datetime = Field(index=True)
    reserved_tokens: int = Field(default=0)
    settled_tokens: int = Field(default=0)
    reserved_cost: float = Field(default=0.0)
    settled_cost: float = Field(default=0.0)
    updated_at: datetime = Field(default_factory=utcnow)

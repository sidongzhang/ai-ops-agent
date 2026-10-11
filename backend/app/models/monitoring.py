"""指标样本（趋势巡检用）：定时采样 → 线性外推 → 未到阈值就提前预警。

阈值告警回答"已经坏了"；趋势预警回答"**快要坏了**"——
例如「MySQL 连接数 3 天涨了 60%，按此趋势约 2 天后打满」。
"""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from .common import utcnow


class MetricSample(SQLModel, table=True):
    __tablename__ = "metric_samples"

    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(index=True)
    system_id: int = Field(index=True)
    metric: str = Field(index=True, max_length=64)   # 见 trend.METRIC_SPECS
    value: float = Field(default=0.0)
    observed_at: datetime = Field(default_factory=utcnow, index=True)

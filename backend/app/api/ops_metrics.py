"""平台自身指标：`GET /metrics`（Prometheus 文本格式）。

与 `/systems/{id}/metrics`（被监控系统的业务指标）不同，这里暴露的是**控制面自己**
的健康与失败信号：数据库连通性、审计写入失败、沙箱拒绝执行、未知连接器。
"""
import secrets

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import text

from ..core import metrics
from ..core.config import settings
from ..core.database import engine

router = APIRouter(tags=["ops-metrics"])


@router.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics(authorization: str = Header(default="")):
    if settings.metrics_token:
        expected = f"Bearer {settings.metrics_token}"
        if not secrets.compare_digest(authorization, expected):
            raise HTTPException(401, "无效的指标访问令牌")

    metrics.set_gauge("aiops_up", 1)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        metrics.set_gauge("aiops_db_up", 1)
    except Exception:  # noqa: BLE001 - 数据库挂了也要能抓到指标
        metrics.set_gauge("aiops_db_up", 0)
    return metrics.render()

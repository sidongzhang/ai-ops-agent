"""
平台 → 采集器下行命令接口。

用户或 AI Agent 调用此接口，通过已建立的 WebSocket 向指定系统的采集器下发命令。
采集器在客户内网执行后，结果实时回传并同步返回给调用方。

支持命令：
  fetch_logs   {"service": "web", "lines": 50}         → 返回日志文本
  search_logs  {"service": "web", "keyword": "ERROR"}  → 返回匹配行
  health_check {"service": "web"}                      → 返回健康状态列表（空 service = 全部）
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..deps import get_current_org_id
from ..models import Collector, MonitoredSystem
from ..schemas import CollectorExecRequest, CollectorExecResponse
from ..ws.manager import manager

router = APIRouter(prefix="/systems", tags=["collector-exec"])
log = logging.getLogger(__name__)

_ALLOWED_CMDS = {"fetch_logs", "search_logs", "health_check"}


@router.post("/{system_id}/collector/exec", response_model=CollectorExecResponse)
async def collector_exec(
    system_id: int,
    body: CollectorExecRequest,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    """向该系统的在线采集器下发命令，同步等待结果（最长 30s）。"""
    if body.cmd not in _ALLOWED_CMDS:
        raise HTTPException(400, f"不支持的命令「{body.cmd}」，可用: {', '.join(_ALLOWED_CMDS)}")

    # 确认系统属于该租户
    system = session.exec(
        select(MonitoredSystem).where(
            MonitoredSystem.id == system_id,
            MonitoredSystem.org_id == org_id,
        )
    ).first()
    if not system:
        raise HTTPException(404, "系统不存在")

    # 找对应采集器
    collector = session.exec(
        select(Collector).where(Collector.system_id == system_id)
    ).first()
    if not collector:
        raise HTTPException(404, "该系统还没有采集器，请先在控制台创建")

    if not manager.is_connected(collector.id):
        raise HTTPException(503, "采集器当前离线（WebSocket 未连接），请确认采集器进程正在运行")

    try:
        result = await manager.send_command(collector.id, body.cmd, body.args)
    except RuntimeError as e:
        raise HTTPException(503, str(e))

    log.info(f"[exec] system={system_id} cmd={body.cmd} ok={result.get('ok')}")
    return CollectorExecResponse(
        ok=result.get("ok", False),
        result=result.get("result", ""),
        collector_id=collector.id,
    )

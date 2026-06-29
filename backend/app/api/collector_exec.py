"""
平台 → 采集器下行命令接口。

用户或 AI Agent 调用此接口，通过已建立的 WebSocket 向指定系统的采集器下发命令。
采集器在客户内网执行后，结果实时回传并同步返回给调用方。

支持命令：
  fetch_logs   {"service": "web", "lines": 50}         → 返回日志文本
  search_logs  {"service": "web", "keyword": "ERROR"}  → 返回匹配行
  health_check {"service": "web"}                      → 返回健康状态列表（空 service = 全部）
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from ..core.database import get_session
from ..core.deps import get_current_org_id
from ..schemas import CollectorExecRequest, CollectorExecResponse
from ..services.collectors.exec import execute_collector_command

router = APIRouter(prefix="/systems", tags=["collector-exec"])


@router.post("/{system_id}/collector/exec", response_model=CollectorExecResponse)
async def collector_exec(
    system_id: int,
    body: CollectorExecRequest,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    try:
        return await execute_collector_command(session, system_id, org_id, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))

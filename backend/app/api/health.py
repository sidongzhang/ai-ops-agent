"""监控端点：返回系统健康。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..core.database import get_session
from ..core.deps import get_current_org_id
from ..schemas import SystemHealth
from ..schemas import CollectorExecRequest
from ..services.collectors.exec import execute_collector_command
from ..services.descriptors.builder import system_to_descriptor
from ..services.descriptors.health import read_service_logs, search_service_logs
from ..services.monitoring.health import get_system_health
from ..services.systems.service import require_system
from ..repositories.systems import list_enabled_services_for_system

router = APIRouter(prefix="/systems", tags=["monitoring"])


@router.get("/{system_id}/health", response_model=SystemHealth)
def system_health(system_id: int, session: Session = Depends(get_session),
                  org_id: int = Depends(get_current_org_id)):
    try:
        return get_system_health(session, system_id, org_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/{system_id}/logs")
async def service_logs(
    system_id: int,
    service: str = Query(..., min_length=1),
    keyword: str = Query("", max_length=100),
    lines: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    """查看已注册服务日志；远程系统通过采集器出站通道读取。"""
    try:
        system = require_system(session, system_id, org_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))

    services = list_enabled_services_for_system(session, system.id)
    if not any(item.name == service for item in services):
        raise HTTPException(404, "服务不存在或尚未启用")

    if system.local:
        descriptor = system_to_descriptor(system, services)
        if keyword:
            content = search_service_logs(descriptor, service, keyword, lines)
        else:
            content = read_service_logs(descriptor, service, lines)
        return {"system_id": system.id, "service": service, "source": "local", "content": content}

    command = "search_logs" if keyword else "fetch_logs"
    args = {"service": service, "lines": lines}
    if keyword:
        args["keyword"] = keyword
    try:
        result = await execute_collector_command(
            session, system.id, org_id, CollectorExecRequest(cmd=command, args=args)
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    return {
        "system_id": system.id,
        "service": service,
        "source": "collector",
        "content": result.result,
    }

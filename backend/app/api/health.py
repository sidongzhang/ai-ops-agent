"""监控端点：返回系统健康。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from ..core.database import get_session
from ..core.deps import get_current_org_id
from ..schemas import SystemHealth
from ..services.monitoring.health import get_system_health

router = APIRouter(prefix="/systems", tags=["monitoring"])


@router.get("/{system_id}/health", response_model=SystemHealth)
def system_health(system_id: int, session: Session = Depends(get_session),
                  org_id: int = Depends(get_current_org_id)):
    try:
        return get_system_health(session, system_id, org_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))

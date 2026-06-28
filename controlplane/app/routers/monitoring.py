"""监控端点：返回系统健康。采集器模式读上报快照，否则平台直连探测。"""
from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..db import get_session
from ..deps import get_current_org_id
from ..descriptors import collect_health, system_to_descriptor
from ..models import Collector
from ..schemas import HealthItem, SystemHealth
from .systems import _services_of, get_org_system

router = APIRouter(prefix="/systems", tags=["monitoring"])


@router.get("/{system_id}/health", response_model=SystemHealth)
def system_health(system_id: int, session: Session = Depends(get_session),
                  org_id: int = Depends(get_current_org_id)):
    system = get_org_system(session, system_id, org_id)

    # 该系统是否由采集器监控（私有内网）。有采集器且有上报 → 用上报快照。
    has_collector = session.exec(
        select(Collector).where(Collector.system_id == system.id)
    ).first()
    if has_collector and system.last_report_at:
        items = [HealthItem(**h) for h in system.last_health.get("services", [])]
        return SystemHealth(
            system_id=system.id, name=system.name,
            healthy=all(i.ok for i in items) if items else True,
            services=items, source="collector",
            reported_at=system.last_report_at.isoformat(),
        )

    # 否则平台直连探测（公网/同网系统）
    descriptor = system_to_descriptor(system, _services_of(session, system.id))
    health = collect_health(descriptor)
    return SystemHealth(
        system_id=system.id, name=system.name,
        healthy=all(h["ok"] for h in health) if health else True,
        services=[HealthItem(**h) for h in health], source="direct",
    )

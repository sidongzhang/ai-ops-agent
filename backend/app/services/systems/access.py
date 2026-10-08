"""系统访问与采集器查找（最底层，无内部依赖）。"""
from sqlmodel import Session, select

from app.models.collectors import Collector
from app.models.systems import MonitoredSystem
from app.repositories.systems import get_system_for_org
from app.services.realtime.websocket import manager


def require_system(session: Session, system_id: int, org_id: int) -> MonitoredSystem:
    system = get_system_for_org(session, system_id, org_id)
    if not system:
        raise LookupError("系统不存在")
    return system


def get_primary_collector(session: Session, system_id: int) -> Collector | None:
    collectors = list(session.exec(select(Collector).where(Collector.system_id == system_id)))
    connected = [collector for collector in collectors if manager.is_connected(collector.id)]
    if connected:
        return max(
            connected,
            key=lambda item: (item.last_seen is not None, item.last_seen, item.id or 0),
        )
    return max(
        collectors,
        key=lambda item: (item.last_seen is not None, item.last_seen, item.id or 0),
        default=None,
    )

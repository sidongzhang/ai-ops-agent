"""
采集器侧接口（X-Collector-Key 鉴权）。
采集器出站调用这两个端点：拉取「该探什么」+ 推送「探活结果」。
平台不需要入站访问客户，凭这套反向上报触达私有内网系统。
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..db import get_session
from ..deps import get_current_collector
from ..descriptors import system_to_descriptor
from ..models import Collector, MonitoredSystem, Service
from ..schemas import CollectorConfig, CollectorReport

router = APIRouter(prefix="/collector", tags=["collector-agent"])


@router.get("/config", response_model=CollectorConfig)
def get_config(collector: Collector = Depends(get_current_collector),
               session: Session = Depends(get_session)):
    system = session.get(MonitoredSystem, collector.system_id)
    services = list(session.exec(select(Service).where(Service.system_id == system.id)))
    descriptor = system_to_descriptor(system, services)
    return CollectorConfig(
        system_id=system.id, name=system.name, local=system.local,
        infra=system.infra or {}, services=descriptor["services"],
    )


@router.post("/report")
def report(body: CollectorReport,
           collector: Collector = Depends(get_current_collector),
           session: Session = Depends(get_session)):
    now = datetime.now(timezone.utc)
    system = session.get(MonitoredSystem, collector.system_id)
    system.last_health = {"services": [s.model_dump() for s in body.services]}
    system.last_report_at = now
    collector.last_seen = now
    session.add(system)
    session.add(collector)
    session.commit()
    return {"ok": True, "received": len(body.services)}

"""系统注册 CRUD。所有操作严格按当前用户的 org 隔离。"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..db import get_session
from ..deps import get_current_org_id
from ..models import MonitoredSystem, Service
from ..schemas import ServiceOut, SystemCreate, SystemOut

router = APIRouter(prefix="/systems", tags=["systems"])


def get_org_system(session: Session, system_id: int, org_id: int) -> MonitoredSystem:
    """按 org 取系统；不属于当前 org 一律 404（避免泄露存在性）。"""
    system = session.get(MonitoredSystem, system_id)
    if not system or system.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "系统不存在")
    return system


def _services_of(session: Session, system_id: int) -> list[Service]:
    return list(session.exec(select(Service).where(Service.system_id == system_id)))


def _to_out(system: MonitoredSystem, services: list[Service]) -> SystemOut:
    return SystemOut(
        id=system.id, org_id=system.org_id, key=system.key, name=system.name,
        local=system.local, notify=system.notify, infra=system.infra,
        services=[ServiceOut(id=s.id, name=s.name, connector=s.connector, config=s.config)
                  for s in services],
    )


@router.post("", response_model=SystemOut, status_code=status.HTTP_201_CREATED)
def create_system(body: SystemCreate, session: Session = Depends(get_session),
                  org_id: int = Depends(get_current_org_id)):
    dup = session.exec(
        select(MonitoredSystem).where(
            MonitoredSystem.org_id == org_id, MonitoredSystem.key == body.key)
    ).first()
    if dup:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"key「{body.key}」在本组织下已存在")

    system = MonitoredSystem(org_id=org_id, key=body.key, name=body.name,
                             local=body.local, notify=body.notify, infra=body.infra)
    session.add(system)
    session.commit()
    session.refresh(system)

    services = []
    for svc in body.services:
        s = Service(system_id=system.id, name=svc.name, connector=svc.connector, config=svc.config)
        session.add(s)
        services.append(s)
    session.commit()
    for s in services:
        session.refresh(s)

    return _to_out(system, services)


@router.get("", response_model=list[SystemOut])
def list_systems(session: Session = Depends(get_session),
                 org_id: int = Depends(get_current_org_id)):
    systems = session.exec(select(MonitoredSystem).where(MonitoredSystem.org_id == org_id)).all()
    return [_to_out(s, _services_of(session, s.id)) for s in systems]


@router.get("/{system_id}", response_model=SystemOut)
def get_system(system_id: int, session: Session = Depends(get_session),
               org_id: int = Depends(get_current_org_id)):
    system = get_org_system(session, system_id, org_id)
    return _to_out(system, _services_of(session, system.id))

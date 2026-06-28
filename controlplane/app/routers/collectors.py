"""采集器管理（用户侧，JWT）。为某套系统创建/列出采集器。"""
from fastapi import APIRouter, Depends, status
from sqlmodel import Session, select

from ..db import get_session
from ..deps import get_current_org_id
from ..models import Collector
from ..schemas import CollectorCreate, CollectorCreated, CollectorOut
from ..security import generate_collector_key, hash_collector_key
from .systems import get_org_system

router = APIRouter(prefix="/systems/{system_id}/collectors", tags=["collectors"])


@router.post("", response_model=CollectorCreated, status_code=status.HTTP_201_CREATED)
def create_collector(system_id: int, body: CollectorCreate,
                     session: Session = Depends(get_session),
                     org_id: int = Depends(get_current_org_id)):
    system = get_org_system(session, system_id, org_id)
    key = generate_collector_key()
    collector = Collector(org_id=org_id, system_id=system.id, name=body.name,
                          token_hash=hash_collector_key(key))
    session.add(collector)
    session.commit()
    session.refresh(collector)
    # 原文密钥仅此一次返回；安装采集器时填入
    return CollectorCreated(id=collector.id, name=collector.name,
                            system_id=system.id, collector_key=key)


@router.get("", response_model=list[CollectorOut])
def list_collectors(system_id: int, session: Session = Depends(get_session),
                    org_id: int = Depends(get_current_org_id)):
    system = get_org_system(session, system_id, org_id)
    collectors = session.exec(select(Collector).where(Collector.system_id == system.id)).all()
    return [
        CollectorOut(id=c.id, name=c.name, system_id=c.system_id,
                     last_seen=c.last_seen.isoformat() if c.last_seen else None)
        for c in collectors
    ]

"""
采集器侧接口（X-Collector-Key 鉴权）。
采集器出站调用这两个端点：拉取「该探什么」+ 推送「探活结果」。
平台不需要入站访问客户，凭这套反向上报触达私有内网系统。
"""
from fastapi import APIRouter, Depends
from sqlmodel import Session

from ..core.database import get_session
from ..core.deps import get_current_collector
from ..models.collectors import Collector
from ..schemas import CollectorConfig, CollectorReport
from ..services.collectors.service import get_collector_config, record_collector_report

router = APIRouter(prefix="/collector", tags=["collector-agent"])


@router.get("/config", response_model=CollectorConfig)
def get_config(collector: Collector = Depends(get_current_collector),
               session: Session = Depends(get_session)):
    return get_collector_config(session, collector)


@router.post("/report")
def report(body: CollectorReport,
           collector: Collector = Depends(get_current_collector),
           session: Session = Depends(get_session)):
    return record_collector_report(session, collector, body)

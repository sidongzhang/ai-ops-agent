"""AI 诊断端点：对已注册系统跑 Pydantic AI agent。"""
from fastapi import APIRouter, Depends
from sqlmodel import Session

from ..agent.diagnose import diagnose
from ..db import get_session
from ..deps import get_current_org_id
from ..descriptors import system_to_descriptor
from ..schemas import DiagnoseRequest, DiagnoseResponse
from .systems import _services_of, get_org_system

router = APIRouter(prefix="/systems", tags=["agent"])


@router.post("/{system_id}/diagnose", response_model=DiagnoseResponse)
def diagnose_system(system_id: int, body: DiagnoseRequest,
                    session: Session = Depends(get_session),
                    org_id: int = Depends(get_current_org_id)):
    system = get_org_system(session, system_id, org_id)
    descriptor = system_to_descriptor(system, _services_of(session, system.id))
    answer = diagnose(descriptor, body.question, org_id=org_id, system_id=system.id)
    return DiagnoseResponse(system_id=system.id, answer=answer)

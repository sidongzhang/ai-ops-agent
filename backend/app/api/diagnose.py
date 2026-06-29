"""AI 诊断端点：对已注册系统跑 Pydantic AI agent。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from ..core.database import get_session
from ..core.deps import get_current_org_id
from ..schemas import DiagnoseRequest, DiagnoseResponse
from ..services.diagnostics.service import diagnose_system as diagnose_system_record

router = APIRouter(prefix="/systems", tags=["agent"])


@router.post("/{system_id}/diagnose", response_model=DiagnoseResponse)
def diagnose_system(system_id: int, body: DiagnoseRequest,
                    session: Session = Depends(get_session),
                    org_id: int = Depends(get_current_org_id)):
    try:
        return diagnose_system_record(session, system_id, org_id, body.question)
    except LookupError as exc:
        raise HTTPException(404, str(exc))

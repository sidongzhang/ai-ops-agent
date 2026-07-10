"""AI 诊断端点：对已注册系统跑 Pydantic AI agent。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from ..core.database import get_session
from ..core.deps import get_current_org_id, get_current_user
from ..models.auth import User
from ..schemas import (
    DiagnosticTemplateOut,
    DiagnosticTemplateSettingsUpdate,
    DiagnoseRequest,
    DiagnoseResponse,
)
from ..services.diagnostics.service import (
    diagnose_system as diagnose_system_record,
    list_diagnostic_templates,
    update_diagnostic_templates,
)

router = APIRouter(prefix="/systems", tags=["agent"])


@router.post("/{system_id}/diagnose", response_model=DiagnoseResponse)
def diagnose_system(system_id: int, body: DiagnoseRequest,
                    session: Session = Depends(get_session),
                    org_id: int = Depends(get_current_org_id),
                    user: User = Depends(get_current_user)):
    try:
        return diagnose_system_record(
            session,
            system_id,
            org_id,
            body.question,
            actor_id=str(user.id),
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/{system_id}/diagnostic-templates", response_model=list[DiagnosticTemplateOut])
def get_templates(
    system_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    try:
        return list_diagnostic_templates(session, system_id, org_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.put("/{system_id}/diagnostic-templates", response_model=list[DiagnosticTemplateOut])
def update_templates(
    system_id: int,
    body: DiagnosticTemplateSettingsUpdate,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(get_current_user),
):
    try:
        return update_diagnostic_templates(
            session,
            system_id,
            org_id,
            body,
            actor_id=str(user.id),
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

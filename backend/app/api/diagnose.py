"""AI 诊断端点：对已注册系统跑 Pydantic AI agent。"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session

from ..core.database import get_session
from ..core.deps import get_current_org_id, get_current_user, require_operator
from ..models.auth import User
from ..agent.llm import model_options
from ..schemas import (
    DiagnosticTemplateOut,
    DiagnosticTemplateSettingsUpdate,
    DiagnoseRequest,
    DiagnoseResponse,
    DiagnosisHistoryClearOut,
    DiagnosisReportOut,
    KnowledgeExportRequest,
    KnowledgeDocOut,
)
from ..services.diagnostics.service import (
    complete_diagnosis_report,
    list_diagnostic_templates,
    start_diagnosis,
    update_diagnostic_templates,
)
from ..services.diagnostics.reports import (
    clear_diagnosis_reports,
    export_report_to_knowledge,
    get_diagnosis_report,
    list_diagnosis_reports,
)

router = APIRouter(prefix="/systems", tags=["agent"])


@router.post("/{system_id}/diagnose", response_model=DiagnoseResponse)
def diagnose_system(
    system_id: int,
    body: DiagnoseRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(get_current_user),
):
    try:
        started = start_diagnosis(
            session,
            system_id,
            org_id,
            body.question,
            actor_id=str(user.id),
        )
        if started.id is None:
            raise HTTPException(500, "诊断任务创建失败")
        background_tasks.add_task(
            complete_diagnosis_report,
            started.id,
            system_id,
            org_id,
            body.question,
            actor_id=str(user.id),
            model_mode=body.model_mode,
            model_name=body.model_name,
        )
        return started
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/{system_id}/diagnose/model-options")
def get_diagnose_model_options(
    system_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    from ..services.systems.service import require_system

    try:
        require_system(session, system_id, org_id)
        return {"options": model_options()}
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/{system_id}/diagnosis-reports", response_model=list[DiagnosisReportOut])
def list_diagnosis_history(
    system_id: int,
    limit: int = 50,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    try:
        return list_diagnosis_reports(session, system_id, org_id, limit=limit)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/{system_id}/diagnosis-reports/{report_id}", response_model=DiagnosisReportOut)
def get_diagnosis_history_item(
    system_id: int,
    report_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    try:
        return get_diagnosis_report(session, system_id, org_id, report_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post(
    "/{system_id}/diagnosis-reports/{report_id}/export-knowledge",
    response_model=KnowledgeDocOut,
)
def export_diagnosis_to_knowledge(
    system_id: int,
    report_id: int,
    body: KnowledgeExportRequest | None = None,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(require_operator),
):
    try:
        from app.services.audit import record_audit_event

        doc = export_report_to_knowledge(
            session,
            system_id,
            org_id,
            report_id,
            doc_name=(body.doc_name if body else ""),
        )
        record_audit_event(
            session,
            org_id=org_id,
            system_id=system_id,
            actor_type="user",
            actor_id=str(user.id),
            event_type="knowledge.exported_from_diagnosis",
            target_type="knowledge_doc",
            target_id=doc.name,
            status="success",
            input={"report_id": report_id, "doc_name": doc.name},
        )
        session.commit()
        return doc
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.delete("/{system_id}/diagnosis-reports", response_model=DiagnosisHistoryClearOut)
def clear_diagnosis_history(
    system_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(get_current_user),
):
    try:
        deleted = clear_diagnosis_reports(session, system_id, org_id)
        return DiagnosisHistoryClearOut(deleted=deleted)
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
    user: User = Depends(require_operator),
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

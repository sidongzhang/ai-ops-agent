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
    ResumeRequest,
    DiagnosisHistoryClearOut,
    DiagnosisReportOut,
    KnowledgeExportRequest,
    KnowledgeDocOut,
)
from ..services.diagnostics.attachments import (
    AttachmentError,
    describe_images,
    parse_image_data_urls,
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
        images = parse_image_data_urls(body.images)
    except AttachmentError as exc:
        raise HTTPException(400, str(exc))
    from ..services.diagnostics import cancel_bus

    if cancel_bus.is_shutting_down():
        # 关停中：拒绝新诊断（避免"刚提交就被杀"的坏体验）
        raise HTTPException(503, "服务正在关停，请稍后重试", headers={"Retry-After": "5"})
    from ..services.diagnostics.usage import BudgetExceeded

    try:
        started = start_diagnosis(
            session,
            system_id,
            org_id,
            body.question,
            actor_id=str(user.id),
            business_context={"images": describe_images(images)} if images else None,
        )
        if started.id is None:
            raise HTTPException(500, "诊断任务创建失败")
        complete_kwargs = {
            "actor_id": str(user.id),
            "model_mode": body.model_mode,
            "model_name": body.model_name,
        }
        if images:
            complete_kwargs["images"] = images
        if body.follow_up_report_id:
            complete_kwargs["follow_up_report_id"] = body.follow_up_report_id
        background_tasks.add_task(
            complete_diagnosis_report,
            started.id,
            system_id,
            org_id,
            body.question,
            **complete_kwargs,
        )
        return started
    except BudgetExceeded as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "30"})
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{system_id}/diagnosis-reports/{report_id}/cancel")
def cancel_diagnosis(
    system_id: int,
    report_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(get_current_user),
):
    """中止正在跑的诊断（幂等）。

    语义：
      * 终态（success/failed/cancelled）→ **no-op**，返回最终状态（不把成功覆盖成取消）。
      * running → 置 `cancel_requested_at`（条件更新，重复点击只有一次写入成功）+ 发布取消消息
        （跨进程加速，可丢）+ 本进程直接取消 token（快路径）。
      * `stopping` 是**派生态**：`cancel_requested_at` 非空且 `status == "running"`；
        对外仍返回 `status: "running"`，不改写 status 字段，保证旧前端/外部链路轮询语义不变。
    """
    from app.models.diagnostics import DiagnosisReport
    from app.services.audit import record_audit_event
    from app.services.diagnostics import cancel_bus

    report = session.get(DiagnosisReport, report_id)
    if not report or report.org_id != org_id or report.system_id != system_id:
        raise HTTPException(404, "诊断报告不存在")

    if report.status != "running":
        return {
            "report_id": report_id,
            "status": report.status,
            "stopping": False,
            "noop": True,
            "newly_set": False,
        }

    try:
        newly = cancel_bus.set_cancel_requested(report_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"取消标志写入失败，请重试：{exc}")

    cancel_bus.publish_cancel(report_id)          # 跨进程（可丢，有轮询兜底）
    cancelled_locally = cancel_bus.request_local_cancel(report_id)  # 同进程快路径

    record_audit_event(
        session,
        org_id=org_id,
        system_id=system_id,
        actor_type="user",
        actor_id=str(user.id) if user else "",
        event_type="diagnosis.cancel_requested",
        target_type="diagnosis",
        target_id=str(report_id),
        input={"newly_set": newly, "local_token_hit": cancelled_locally},
        commit=True,
    )
    # 状态仍是 running：前端据此显示"正在停止"，等运行方落 cancelled
    return {
        "report_id": report_id,
        "status": "running",
        "stopping": True,
        "noop": False,
        "newly_set": newly,
        "local_token_hit": cancelled_locally,
    }


@router.post("/{system_id}/diagnosis-reports/{report_id}/resume", response_model=DiagnoseResponse)
def resume_diagnosis(
    system_id: int,
    report_id: int,
    background_tasks: BackgroundTasks,
    body: ResumeRequest | None = None,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(get_current_user),
):
    """继续一个被中断的诊断（**幂等**：同一源报告只会真正启动一次）。

    - 源报告不可恢复（无历史/历史损坏/已成功）→ 409；
    - 已被继续过 → 直接返回那条（不重复执行、不重复花钱）；
    - 预算不足 → 429。
    """
    from ..services.diagnostics.resume import ResumeNotPossible
    from ..services.diagnostics.service import start_resume
    from ..services.diagnostics.usage import BudgetExceeded

    opts = body or ResumeRequest()
    try:
        started, payload = start_resume(
            session,
            report_id,
            system_id,
            org_id,
            actor_id=str(user.id) if user else "",
            force_refresh=opts.force_refresh,
        )
    except ResumeNotPossible as exc:
        raise HTTPException(409, str(exc))
    except BudgetExceeded as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "30"})
    except LookupError as exc:
        raise HTTPException(404, str(exc))

    if payload is not None and started.id is not None:
        background_tasks.add_task(
            complete_diagnosis_report,
            started.id,
            system_id,
            org_id,
            "",
            actor_id=str(user.id) if user else "",
            model_mode=opts.model_mode,
            model_name=opts.model_name,
            resume_payload=payload,
        )
    return started


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
    from ..services.diagnostics.resume import RESUMABLE_STATUSES
    from ..services.diagnostics.run_state import resumable_of

    try:
        report = get_diagnosis_report(session, system_id, org_id, report_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    # resumable：读时计算（历史存在且能反序列化）；can_resume：还要状态允许
    resumable = resumable_of(report_id, session=session)
    # 会话累计：按 conversation_id 从用量账本 SUM(delta) 得到（与"本次消耗"区分）
    conversation_tokens, conversation_cost = _conversation_usage(session, report, system_id, org_id)
    return report.model_copy(
        update={
            "resumable": resumable,
            "can_resume": bool(resumable and report.status in RESUMABLE_STATUSES),
            "conversation_tokens": conversation_tokens,
            "conversation_cost_usd": conversation_cost,
        }
    )


def _conversation_usage(session: Session, report, system_id: int, org_id: int) -> tuple[int, float]:
    """按 conversation_id 汇总会话累计；续跑报告自身没有状态时回溯源报告的会话标识。"""
    from ..models.run_state import DiagnosisRunState
    from ..services.diagnostics.usage import session_usage

    state = session.get(DiagnosisRunState, report.id)
    conversation_id = (state.conversation_id if state else "") or ""
    if not conversation_id and report.resumed_from_report_id:
        src = session.get(DiagnosisRunState, report.resumed_from_report_id)
        conversation_id = (src.conversation_id if src else "") or ""
    if not conversation_id:
        return int(report.total_tokens or 0), float(report.cost_usd_estimated or 0)
    totals = session_usage(org_id, system_id, conversation_id=conversation_id, session=session)
    return int(totals.get("tokens") or 0), float(totals.get("cost_usd") or 0)


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


@router.get("/{system_id}/diagnosis-artifacts/{token}")
def download_diagnosis_artifact(system_id: int, token: str):
    """下载 agent 取回的归档附件（凭不可猜的 token，免登录）。"""
    from fastapi.responses import FileResponse

    from ..services.diagnostics.artifacts import find_artifact

    found = find_artifact(token)
    if not found:
        raise HTTPException(404, "附件不存在或已失效")
    path, name = found
    return FileResponse(path, filename=name, media_type="application/octet-stream")

"""AI 诊断端点：对已注册系统跑 Pydantic AI agent。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
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
    from ..services.diagnostics.routing import decision, NeedsDiagnosis
    try:
        route=decision(session,system_id,org_id,body.question,chat_id=body.chat_id or '',special=bool(images))
    except LookupError as exc:
        raise HTTPException(404,str(exc))
    if route['resolved']=='blocked':
        raise HTTPException(409,{'code':'diagnosis_required','message':str(NeedsDiagnosis(route)),'interaction':route})
    from ..agent.llm import LocalModelUnavailable, ensure_model_ready

    try:
        # 选了本地模型就先做一次廉价就绪检查：没就绪就立刻报错。
        # 否则会"卡在 prefill"很久（实测 7B 放不下内存时 >8 分钟 0 输出、最后被 OOM kill），
        # 用户会以为平台坏了。
        ensure_model_ready(body.model_mode, body.model_name)
    except LocalModelUnavailable as exc:
        raise HTTPException(409, str(exc))
    from ..services.diagnostics.usage import BudgetExceeded
    from ..agent.runtime.factory import get_runtime
    from ..agent.runtime.contracts import RuntimeCompatibilityError
    if body.runtime_engine == "pi":
        try:
            get_runtime("pi").preflight({"images": images, "model_mode": body.model_mode, "model_name": body.model_name})
        except RuntimeCompatibilityError as exc:
            raise HTTPException(409, str(exc))

    try:
        started = start_diagnosis(
            session,
            system_id,
            org_id,
            body.question,
            job_options={"model_mode": body.model_mode, "model_name": body.model_name,
                "runtime_engine": body.runtime_engine, "images": images,
                "follow_up_report_id": body.follow_up_report_id},
            actor_id=str(user.id),
            **({"chat_id": body.chat_id} if body.chat_id else {}),
            business_context={"runtime_engine": body.runtime_engine, "images": describe_images(images)} if body.runtime_engine == "pi" else ({"images": describe_images(images)} if images else None),
        )
        if started.id is None:
            raise HTTPException(500, "诊断任务创建失败")
        return started
    except NeedsDiagnosis as exc:
        raise HTTPException(409,{'code':'diagnosis_required','message':str(exc),'interaction':exc.route})
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
    from ..services.diagnostics.cancel_bus import INSTANCE_ID as _worker_id

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
    session.refresh(report)
    # 排队取消可立即落终态；已执行任务等待运行方确认。
    return {
        "report_id": report_id,
        "status": report.status,
        "stopping": report.status == "running",
        "noop": False,
        "newly_set": newly,
        "local_token_hit": cancelled_locally,
        # 处理本次取消的进程；与报告的 runner_id 不同 → 证明确实是**跨进程**取消生效
        "handled_by": _worker_id,
        "runner_id": report.runner_id or "",
    }


@router.post("/{system_id}/diagnosis-reports/{report_id}/resume", response_model=DiagnoseResponse)
def resume_diagnosis(
    system_id: int,
    report_id: int,
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
    from ..agent.llm import LocalModelUnavailable, ensure_model_ready

    try:
        ensure_model_ready(opts.model_mode, opts.model_name)
    except LocalModelUnavailable as exc:
        raise HTTPException(409, str(exc))
    from ..models.run_state import DiagnosisRunState
    state=session.get(DiagnosisRunState,report_id)
    if state is not None and state.engine=="pi":
        try:
            get_diagnosis_report(session,system_id,org_id,report_id)
            from ..agent.runtime.factory import get_runtime
            get_runtime("pi").preflight({"model_mode":opts.model_mode,"model_name":opts.model_name})
        except LookupError:
            raise HTTPException(404,"诊断报告不存在")
        except ValueError as exc:
            raise HTTPException(409,str(exc))
    try:
        started, payload = start_resume(
            session,
            report_id,
            system_id,
            org_id,
            actor_id=str(user.id) if user else "",
            force_refresh=opts.force_refresh,
            job_options={"model_mode": opts.model_mode, "model_name": opts.model_name},
        )
    except ResumeNotPossible as exc:
        raise HTTPException(409, str(exc))
    except BudgetExceeded as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "30"})
    except LookupError as exc:
        raise HTTPException(404, str(exc))

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
        options = model_options()
        # 给「本地模型」标出是否就绪，前端可直接禁用并显示原因（避免用户选了才发现跑不了）。
        from ..agent.llm import endpoint_for_mode, local_model_status

        for item in options:
            if item.get("value") == "local":
                reason = local_model_status(endpoint_for_mode("local"), timeout=1.5)
                item["available"] = reason is None
                if reason:
                    item["reason"] = reason
        return {"options": options}
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
    except ValueError as exc:
        raise HTTPException(409, str(exc))
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
    """下载 agent 取回的归档附件。

    凭不可猜的 token 访问（能力 URL），但必须与该附件签发时的 system_id 一致，
    且受 ARTIFACT_TTL_HOURS 约束：过期、跨系统、无元数据一律 404。
    """
    from fastapi.responses import FileResponse

    from ..services.diagnostics.artifacts import find_artifact

    found = find_artifact(token, system_id=system_id)
    if not found:
        raise HTTPException(404, "附件不存在或已失效")
    path, name = found
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    return FileResponse(path, filename=name, media_type="application/octet-stream", headers=headers)


@router.get('/{system_id}/diagnosis-reports/{report_id}/events')
def diagnosis_events(system_id: int, report_id: int, after_sequence: int = 0,
                     session: Session = Depends(get_session), org_id: int = Depends(get_current_org_id)):
    # Existing report lookup performs org/system authorization before returning events.
    try:
        get_diagnosis_report(session, system_id, org_id, report_id)
    except LookupError:
        raise HTTPException(404,"诊断报告不存在")
    from app.services.diagnostics.runtime_events import replay_events
    rows=replay_events(session,str(report_id),org_id,system_id,max(0,after_sequence))
    return {'events':[{'run_id':row.run_id,'sequence':row.sequence,'kind':row.kind,'payload':row.payload,
                       'timestamp':row.created_at.isoformat()} for row in rows]}


@router.get('/{system_id}/diagnosis-reports/{report_id}/stream')
def diagnosis_answer_stream(request: Request, system_id: int, report_id: int,
                            session: Session = Depends(get_session),
                            org_id: int = Depends(get_current_org_id)):
    try:
        get_diagnosis_report(session, system_id, org_id, report_id)
    except LookupError:
        raise HTTPException(404, "诊断报告不存在")
    from app.services.diagnostics.stream import report_events
    return StreamingResponse(report_events(request, report_id, system_id, org_id,
                                           request.headers.get('last-event-id', '')),
                             media_type='text/event-stream',
                             headers={'Cache-Control':'no-cache', 'X-Accel-Buffering':'no'})

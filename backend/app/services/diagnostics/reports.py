"""CRUD for persisted diagnosis reports."""
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from app.agent.diagnostics.knowledge.store import _docs_dir, invalidate_index
from app.models.diagnostics import DiagnosisReport
from app.schemas.diagnostics import DiagnosisReportOut
from app.schemas import KnowledgeDocOut
from app.services.systems.service import require_system


def save_diagnosis_report(
    session: Session,
    *,
    org_id: int,
    system_id: int,
    user_id: int | None,
    report_type: str,
    question: str,
    external_request_id: str = "",
    answer: str = "",
    status: str = "success",
    template_name: str = "",
    template_description: str = "",
    model: str = "",
    duration_ms: int = 0,
    total_tokens: int = 0,
    evidence_sources: list[str] | None = None,
    evidence_steps: list[str] | None = None,
    tool_calls: list[dict] | None = None,
    evidence: list[dict] | None = None,
    knowledge_refs: list[dict] | None = None,
    business_context: dict | None = None,
    error_message: str = "",
    resumed_from_report_id: int | None = None,
    commit: bool = True,
) -> DiagnosisReport:
    report = DiagnosisReport(
        org_id=org_id,
        system_id=system_id,
        user_id=user_id,
        external_request_id=external_request_id,
        report_type=report_type,
        status=status,
        question=question,
        answer=answer,
        template_name=template_name,
        template_description=template_description,
        model=model,
        duration_ms=duration_ms,
        total_tokens=total_tokens,
        evidence_sources=evidence_sources or [],
        evidence_steps=evidence_steps or [],
        tool_calls=tool_calls or [],
        evidence=evidence or [],
        knowledge_refs=knowledge_refs or [],
        business_context=business_context or {},
        error_message=error_message,
        resumed_from_report_id=resumed_from_report_id,
    )
    session.add(report)
    if commit:
        session.commit()
        session.refresh(report)
    else:
        session.flush()
        session.refresh(report)
    return report


def _report_out(row: DiagnosisReport, session: Session | None = None, *, performance_detail=False) -> DiagnosisReportOut:
    """构造出参：`model_dump()` 不包含派生属性，这里把「未取证」标记补上。

    `unverified_reason` 是读取时派生的（见 models.diagnostics），用于把
    "success 但没做任何取证"的报告显式标出来，避免"没干活"被当成"干完了"。
    """
    payload = row.model_dump()
    payload["unverified_reason"] = row.unverified_reason
    # Keep the existing Java map contract; display drafts never enter prompt context.
    context = dict(payload.get("business_context") or {})
    draft = row.answer_draft
    if row.status == "running":
        from .answer_stream import read_cached
        cached = read_cached(row.id)
        if cached and (not draft or cached.get("sequence", 0) >= draft.get("sequence", 0)):
            draft = cached
    if draft: context["answer_stream"] = draft
    payload["business_context"] = context
    if session is not None:
        from .jobs import execution_context
        payload["business_context"] = {**execution_context(session, row), **({"answer_stream": draft} if draft else {})}
        if row.status == "cancelled" and (row.business_context or {}).get("execution_stop_reason") == "run_timeout":
            payload["answer"] = "运行超过 10 分钟，已请求取消并确认停止。可在有可恢复历史时选择继续。"
            payload["error_message"] = "运行超时，已停止"
    if session is not None:
        # Queries and SSE must expose the same checkpoint eligibility as chat
        # history. Schema defaults are not evidence that history was lost.
        from .run_state import resumable_of
        from .resume import RESUMABLE_STATUSES
        from .usage import session_usage
        from app.models.run_state import DiagnosisRunState

        resumable = resumable_of(row.id, session=session)
        payload.update(resumable=resumable, can_resume=bool(resumable and row.status in RESUMABLE_STATUSES))
        state = session.get(DiagnosisRunState, row.id)
        if state and state.conversation_id:
            totals = session_usage(row.org_id, row.system_id, conversation_id=state.conversation_id, session=session)
            if totals:
                payload.update(conversation_tokens=totals["tokens"], conversation_cost_usd=totals["cost_usd"])
    if session is not None:
        from .performance import read as read_performance
        payload['business_context']['performance']=read_performance(session,row,detail=performance_detail)
    return DiagnosisReportOut(**payload)


def list_diagnosis_reports(
    session: Session,
    system_id: int,
    org_id: int,
    *,
    limit: int = 50,
    external_request_id: str = "",
) -> list[DiagnosisReportOut]:
    require_system(session, system_id, org_id)
    limit = max(1, min(limit, 100))
    statement = select(DiagnosisReport).where(
        DiagnosisReport.org_id == org_id,
        DiagnosisReport.system_id == system_id,
    )
    if external_request_id:
        statement = statement.where(DiagnosisReport.external_request_id == external_request_id)
    rows = session.exec(
        statement
        .order_by(DiagnosisReport.created_at.desc())
        .limit(limit)
    ).all()
    rows.reverse()
    return [_report_out(row, session) for row in rows]


def get_diagnosis_report(
    session: Session,
    system_id: int,
    org_id: int,
    report_id: int,
) -> DiagnosisReportOut:
    require_system(session, system_id, org_id)
    report = session.get(DiagnosisReport, report_id)
    if not report or report.org_id != org_id or report.system_id != system_id:
        raise LookupError("诊断报告不存在")
    return _report_out(report, session, performance_detail=True)


def _safe_doc_name(name: str) -> str:
    raw = Path(name).stem or "diagnosis"
    safe = re.sub(r"[^\w\-.\u4e00-\u9fff]+", "-", raw).strip("-._") or "diagnosis"
    return f"{safe[:96]}.md"


def _build_export_markdown(report: DiagnosisReport) -> str:
    lines = [
        f"# 诊断报告 #{report.id}",
        "",
        f"- 类型：{report.report_type}",
        f"- 时间：{report.created_at.isoformat()}",
    ]
    if report.template_name:
        lines.append(f"- 模板：{report.template_name}")
    if report.model:
        lines.append(f"- 模型：{report.model}")
    lines.extend(["", "## 问题", "", report.question.strip(), "", "## 结论", "", report.answer.strip()])
    if report.evidence_steps:
        lines.extend(["", "## 分析步骤"])
        for step in report.evidence_steps:
            lines.append(f"- {step}")
    if report.evidence:
        lines.extend(["", "## 证据明细"])
        for item in report.evidence:
            label = item.get("label") or item.get("type") or "证据"
            detail = item.get("detail") or item.get("summary") or ""
            lines.extend([f"### {label}", "", detail.strip(), ""])
    if report.knowledge_refs:
        lines.extend(["", "## 参考知识库"])
        for ref in report.knowledge_refs:
            title = ref.get("title") or ref.get("name") or "文档"
            content = ref.get("content") or ref.get("snippet") or ""
            lines.extend([f"### {title}", "", content.strip(), ""])
    return "\n".join(lines).strip() + "\n"


def export_report_to_knowledge(
    session: Session,
    system_id: int,
    org_id: int,
    report_id: int,
    *,
    doc_name: str = "",
) -> KnowledgeDocOut:
    require_system(session, system_id, org_id)
    report = session.get(DiagnosisReport, report_id)
    if not report or report.org_id != org_id or report.system_id != system_id:
        raise LookupError("诊断报告不存在")
    default_name = f"diagnosis-{report_id}-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    filename = _safe_doc_name(doc_name or default_name)
    docs_dir = _docs_dir(str(system_id))
    docs_dir.mkdir(parents=True, exist_ok=True)
    path = docs_dir / filename
    path.write_text(_build_export_markdown(report), encoding="utf-8")
    invalidate_index(str(system_id))
    return KnowledgeDocOut(name=filename, size=path.stat().st_size)


def clear_diagnosis_reports(session: Session, system_id: int, org_id: int) -> int:
    require_system(session, system_id, org_id)
    rows = session.exec(
        select(DiagnosisReport).where(
            DiagnosisReport.org_id == org_id,
            DiagnosisReport.system_id == system_id,
        )
    ).all()
    if any(row.status == "running" for row in rows):
        raise ValueError("存在排队或执行中的诊断，请先停止并等待结束后清理历史")
    from sqlalchemy import delete
    from app.models.diagnosis_jobs import DiagnosisJob
    ids = [row.id for row in rows]
    if ids:
        from app.models.diagnosis_performance import DiagnosisPerformance
        session.execute(delete(DiagnosisPerformance).where(DiagnosisPerformance.report_id.in_(ids)))
        session.execute(delete(DiagnosisJob).where(DiagnosisJob.report_id.in_(ids)))
    count = len(rows)
    for row in rows:
        session.delete(row)
    session.commit()
    from .answer_stream import discard_cached
    discard_cached(ids)
    return count

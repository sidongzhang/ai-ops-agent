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
    error_message: str = "",
    commit: bool = True,
) -> DiagnosisReport:
    report = DiagnosisReport(
        org_id=org_id,
        system_id=system_id,
        user_id=user_id,
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
        error_message=error_message,
    )
    session.add(report)
    if commit:
        session.commit()
        session.refresh(report)
    else:
        session.flush()
        session.refresh(report)
    return report


def list_diagnosis_reports(
    session: Session,
    system_id: int,
    org_id: int,
    *,
    limit: int = 50,
) -> list[DiagnosisReportOut]:
    require_system(session, system_id, org_id)
    limit = max(1, min(limit, 100))
    rows = session.exec(
        select(DiagnosisReport)
        .where(DiagnosisReport.org_id == org_id, DiagnosisReport.system_id == system_id)
        .order_by(DiagnosisReport.created_at.desc())
        .limit(limit)
    ).all()
    rows.reverse()
    return [DiagnosisReportOut(**row.model_dump()) for row in rows]


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
    return DiagnosisReportOut(**report.model_dump())


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
    count = len(rows)
    for row in rows:
        session.delete(row)
    session.commit()
    return count

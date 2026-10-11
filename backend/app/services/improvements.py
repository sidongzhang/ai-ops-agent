"""自改进：把线上「差评 / 诊断失败」沉淀成候选，人工采纳后写入结构化记忆。

设计原则：
  * **只有人工采纳才入库**——避免模型自我强化把错误结论当经验（旧版的"记忆污染"）。
  * **候选不阻断主流程**——采集失败只记日志。
  * **采纳即标注来源**——source=human、高置信度，检索时优先级最高。
"""
import logging
from datetime import datetime, timezone

from sqlmodel import Session, select

from ..models.diagnostics import DiagnosisReport
from ..models.improvements import (
    SOURCE_FAILED,
    SOURCE_FEEDBACK_DOWN,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    ImprovementCandidate,
)
from .knowledge.memory import _extract_conclusion, _extract_remedy

log = logging.getLogger(__name__)


def _excerpt(text: str, limit: int) -> str:
    return (text or "").strip()[:limit]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def capture_candidate(
    session: Session,
    *,
    report: DiagnosisReport,
    source: str,
    note: str = "",
) -> ImprovementCandidate | None:
    """从一次诊断报告生成（或合并）自改进候选。"""
    if report is None:
        return None
    answer = report.answer or ""
    symptom = _excerpt(report.question, 500)
    root_cause = _extract_conclusion(answer)
    remedy = _extract_remedy(answer)
    if not symptom and not root_cause:
        return None

    existing = session.exec(
        select(ImprovementCandidate)
        .where(ImprovementCandidate.report_id == report.id)
        .where(ImprovementCandidate.source == source)
        .where(ImprovementCandidate.status == STATUS_PENDING)
    ).first()
    if existing:
        # 同一报告同来源只留一条待审候选；补充最新备注
        if note and note not in (existing.note or ""):
            existing.note = _excerpt(f"{existing.note}\n{note}".strip(), 2000)
            existing.updated_at = _now()
            session.add(existing)
            session.commit()
            session.refresh(existing)
        return existing

    candidate = ImprovementCandidate(
        org_id=report.org_id,
        system_id=report.system_id,
        report_id=report.id,
        source=source,
        status=STATUS_PENDING,
        question=symptom,
        answer_excerpt=_excerpt(answer, 2000),
        symptom=symptom,
        root_cause=root_cause,
        remedy=remedy,
        note=_excerpt(note, 2000),
    )
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    log.info(
        "[improvements] 生成候选 id=%s（system=%s source=%s report=%s）",
        candidate.id, report.system_id, source, report.id,
    )
    return candidate


def capture_failure(report_id: int, error: str = "") -> None:
    """诊断失败时生成候选（在诊断线程里调用，自开会话；失败静默）。"""
    try:
        from ..core.database import engine

        with Session(engine) as session:
            report = session.get(DiagnosisReport, report_id)
            if report is None:
                return
            capture_candidate(session, report=report, source=SOURCE_FAILED, note=error)
    except Exception as exc:  # noqa: BLE001 - 采集失败不影响诊断
        log.debug("[improvements] 失败案例采集异常（忽略）: %s", exc)


def list_candidates(
    session: Session,
    org_id: int,
    *,
    system_id: int | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[ImprovementCandidate]:
    query = select(ImprovementCandidate).where(ImprovementCandidate.org_id == org_id)
    if system_id is not None:
        query = query.where(ImprovementCandidate.system_id == system_id)
    if status:
        query = query.where(ImprovementCandidate.status == status)
    query = query.order_by(ImprovementCandidate.created_at.desc()).limit(limit)
    return list(session.exec(query).all())


def count_pending(session: Session, org_id: int, *, system_id: int | None = None) -> int:
    query = (
        select(ImprovementCandidate)
        .where(ImprovementCandidate.org_id == org_id)
        .where(ImprovementCandidate.status == STATUS_PENDING)
    )
    if system_id is not None:
        query = query.where(ImprovementCandidate.system_id == system_id)
    return len(session.exec(query).all())


def approve(
    session: Session,
    candidate: ImprovementCandidate,
    *,
    reviewer_id: int | None,
    symptom: str | None = None,
    root_cause: str | None = None,
    remedy: str | None = None,
    review_note: str = "",
) -> ImprovementCandidate:
    """采纳候选：写入结构化记忆（source=human、高置信度），并回填 memory_id。"""
    if symptom is not None:
        candidate.symptom = _excerpt(symptom, 500)
    if root_cause is not None:
        candidate.root_cause = _excerpt(root_cause, 2000)
    if remedy is not None:
        candidate.remedy = _excerpt(remedy, 2000)

    from .knowledge.memory import save_memory

    memory_id = save_memory(
        org_id=candidate.org_id,
        system_id=candidate.system_id,
        question=candidate.question,
        report_id=candidate.report_id,
        source="human",
        symptom=candidate.symptom,
        root_cause=candidate.root_cause,
        remedy=candidate.remedy,
    )
    candidate.status = STATUS_APPROVED
    candidate.memory_id = memory_id
    candidate.review_note = _excerpt(review_note, 2000)
    candidate.reviewed_by = reviewer_id
    candidate.reviewed_at = _now()
    candidate.updated_at = _now()
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    log.info("[improvements] 采纳 id=%s → 记忆 id=%s", candidate.id, memory_id)
    return candidate


def reject(
    session: Session,
    candidate: ImprovementCandidate,
    *,
    reviewer_id: int | None,
    review_note: str = "",
) -> ImprovementCandidate:
    candidate.status = STATUS_REJECTED
    candidate.review_note = _excerpt(review_note, 2000)
    candidate.reviewed_by = reviewer_id
    candidate.reviewed_at = _now()
    candidate.updated_at = _now()
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    return candidate

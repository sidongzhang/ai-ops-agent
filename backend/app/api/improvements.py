"""自改进端点：诊断反馈、候选审核（采纳→写记忆 / 拒绝）。"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..core.database import get_session
from ..core.deps import get_current_org_id, require_operator
from ..models.auth import User
from ..models.diagnostics import DiagnosisReport
from ..models.improvements import (
    SOURCE_FEEDBACK_DOWN,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    ImprovementCandidate,
)
from ..schemas.improvements import (
    FeedbackOut,
    FeedbackRequest,
    ImprovementCandidateOut,
    ImprovementRejectRequest,
    ImprovementReviewRequest,
    ImprovementStatsOut,
)
from ..services import improvements as svc
from ..services.systems.service import require_system

router = APIRouter(prefix="/systems", tags=["improvements"])


def _out(candidate: ImprovementCandidate) -> ImprovementCandidateOut:
    return ImprovementCandidateOut(**candidate.model_dump())


def _require_candidate(session: Session, system_id: int, candidate_id: int, org_id: int) -> ImprovementCandidate:
    candidate = session.get(ImprovementCandidate, candidate_id)
    if not candidate or candidate.org_id != org_id or candidate.system_id != system_id:
        raise HTTPException(404, "候选不存在")
    return candidate


@router.post("/{system_id}/diagnosis-reports/{report_id}/feedback", response_model=FeedbackOut)
def submit_feedback(
    system_id: int,
    report_id: int,
    body: FeedbackRequest,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    """提交诊断反馈。点「没用」会自动生成一条自改进候选（待人工审核）。"""
    report = session.get(DiagnosisReport, report_id)
    if not report or report.org_id != org_id or report.system_id != system_id:
        raise HTTPException(404, "诊断报告不存在")

    report.feedback = body.rating
    report.feedback_note = (body.note or "")[:2000]
    report.feedback_at = datetime.now(timezone.utc)
    session.add(report)
    session.commit()

    candidate_id = None
    if body.rating == "down":
        candidate = svc.capture_candidate(
            session, report=report, source=SOURCE_FEEDBACK_DOWN, note=body.note or ""
        )
        candidate_id = candidate.id if candidate else None
    return FeedbackOut(report_id=report.id, feedback=report.feedback, candidate_id=candidate_id)


@router.get("/{system_id}/improvements", response_model=list[ImprovementCandidateOut])
def list_improvements(
    system_id: int,
    status: str | None = Query(None, pattern="^(pending|approved|rejected)$"),
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    require_system(session, system_id, org_id)
    rows = svc.list_candidates(session, org_id, system_id=system_id, status=status, limit=limit)
    return [_out(row) for row in rows]


@router.get("/{system_id}/improvements/stats", response_model=ImprovementStatsOut)
def improvement_stats(
    system_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    require_system(session, system_id, org_id)
    return ImprovementStatsOut(
        pending=svc.count_pending(session, org_id, system_id=system_id),
        approved=len(svc.list_candidates(session, org_id, system_id=system_id, status=STATUS_APPROVED, limit=100000)),
        rejected=len(svc.list_candidates(session, org_id, system_id=system_id, status=STATUS_REJECTED, limit=100000)),
    )


@router.post("/{system_id}/improvements/{candidate_id}/approve", response_model=ImprovementCandidateOut)
def approve_improvement(
    system_id: int,
    candidate_id: int,
    body: ImprovementReviewRequest,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(require_operator),
):
    """采纳候选：可编辑内容后写入结构化记忆（source=human，检索优先）。"""
    candidate = _require_candidate(session, system_id, candidate_id, org_id)
    if candidate.status == STATUS_APPROVED:
        raise HTTPException(400, "该候选已采纳")
    candidate = svc.approve(
        session,
        candidate,
        reviewer_id=user.id if user else None,
        symptom=body.symptom,
        root_cause=body.root_cause,
        remedy=body.remedy,
        review_note=body.review_note,
    )
    return _out(candidate)


@router.post("/{system_id}/improvements/{candidate_id}/reject", response_model=ImprovementCandidateOut)
def reject_improvement(
    system_id: int,
    candidate_id: int,
    body: ImprovementRejectRequest,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(require_operator),
):
    candidate = _require_candidate(session, system_id, candidate_id, org_id)
    if candidate.status == STATUS_APPROVED:
        raise HTTPException(400, "该候选已采纳，不能拒绝")
    candidate = svc.reject(
        session, candidate, reviewer_id=user.id if user else None, review_note=body.review_note
    )
    return _out(candidate)

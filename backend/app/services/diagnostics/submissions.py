"""Database uniqueness arbitrates retries across processes before budget admission."""
import hashlib
import json
from sqlalchemy.exc import IntegrityError
from app.models.submissions import DiagnosisSubmission


class SubmissionConflict(ValueError):
    pass


def claim(session, *, org_id, system_id, token_id, body):
    key = body.idempotency_key
    if not key:
        return None, True
    identity = f"{org_id}:{system_id}:{token_id}:{key}"
    submission_id = hashlib.sha256(identity.encode()).hexdigest()
    # Business snapshots change between retries; only caller intent defines identity.
    intent = body.model_dump(mode="json", exclude={"business_context", "idempotency_key"})
    fingerprint = hashlib.sha256(json.dumps(intent, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    # INSERT, budget reservation, report and outbox commit together. ON CONFLICT
    # waits for the winning transaction without rolling back caller state.
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
    stmt = insert(DiagnosisSubmission).values(id=submission_id, org_id=org_id,
        system_id=system_id, fingerprint=fingerprint).on_conflict_do_nothing(index_elements=["id"])
    inserted = session.execute(stmt).rowcount == 1
    if inserted:
        return submission_id, True
    existing = session.get(DiagnosisSubmission, submission_id)
    if existing is None:
        raise SubmissionConflict("受理状态暂不可用，请重试")
    if existing.fingerprint != fingerprint:
        raise SubmissionConflict("同一幂等键不能用于不同的诊断请求")
    if existing.report_id is None:
        # Legacy committed claims cannot safely be taken over. New atomic
        # admissions never create this state; return an actionable terminal error.
        raise SubmissionConflict("此前受理已中断且没有诊断报告；请重新发起请求并使用新的请求标识，不会自动重跑")
    return submission_id, False


def abandon(session, submission_id):
    # New claims are uncommitted. Never delete another admission's identity.
    session.rollback()

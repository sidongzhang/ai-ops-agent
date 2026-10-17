"""DB outbox + Celery transport. Started work is never automatically replayed."""
import base64
import json
import logging
import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from sqlalchemy import update
from sqlmodel import select
from app.models.diagnosis_jobs import DiagnosisJob
from app.models.diagnostics import DiagnosisReport
from .session import session as db_session

log = logging.getLogger(__name__)
QUEUE = "diagnoses"
WAIT_SECONDS = 15 * 60
RUN_SECONDS = 10 * 60
STOP_GRACE_SECONDS = 30


def now():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def add_job(db, report, question, options):
    options = dict(options)
    images = options.pop("images", None)
    payload = {"question": question, "options": options}
    if images:
        payload["images"] = [[mime, base64.b64encode(data).decode("ascii")] for mime, data in images]
    # Framework usage objects are not broker-serializable. The persisted
    # baseline_json remains authoritative when native history is restored.
    payload = json.loads(json.dumps(payload, default=lambda value: asdict(value) if is_dataclass(value) else _unsupported(value)))
    stamp = now()
    db.add(DiagnosisJob(report_id=report.id, payload=payload, created_at=stamp,
                        expires_at=stamp + timedelta(seconds=WAIT_SECONDS), publish_after=stamp))
    from .performance import pending
    pending(db,report.id)


def _unsupported(value):
    raise TypeError(f"Unsupported diagnosis job payload type: {type(value).__name__}")


def execution_context(db, report):
    context = dict(report.business_context or {})
    job = db.get(DiagnosisJob, report.id)
    if job:
        phase = "queued" if job.state == "pending" else "running"
        if report.cancel_requested_at and report.status == "running":
            phase = "stopping"
        if context.get("execution_stop_unconfirmed"):
            phase = "unknown"
        elif report.status != "running":
            phase = report.status
        context["execution"] = {"phase": phase,
            "wait_seconds": max(0, int((aware(job.started_at or job.finished_at or now()) - aware(job.created_at)).total_seconds())),
            "reason": context.get("execution_stop_reason") or report.error_message,
            "queue_deadline": aware(job.expires_at).isoformat()}
    return context


def finish_pending(db, report_id, *, cancelled=False, reason="排队超过 15 分钟，未执行；请重新发起诊断"):
    """One atomic winner against worker admission; budget release shares the tx."""
    won = db.execute(update(DiagnosisJob).where(DiagnosisJob.report_id == report_id,
        DiagnosisJob.state == "pending").values(state="done", payload={}, finished_at=now()))
    if won.rowcount != 1:
        return False
    report = db.get(DiagnosisReport, report_id)
    if report is not None and report.status == "running":
        report.status = "cancelled" if cancelled else "failed"
        report.error_message = reason
        report.answer = reason
        if cancelled:
            report.cancel_requested_at = report.cancel_requested_at or now()
            report.cancelled_at = now()
        report.cancel_phase = "idle"
        report.usage_complete = True
        report.cost_usd_estimated = 0
        if report.resumed_from_report_id:
            # Cancelling/expiring a queued continuation must not strand the
            # original native checkpoint behind its unique resume link.
            from app.models.run_state import DiagnosisRunState
            source_state = db.get(DiagnosisRunState, report.resumed_from_report_id)
            if source_state and source_state.resumable:
                inherited = source_state.model_dump(exclude={"report_id", "updated_at"})
                db.add(DiagnosisRunState(report_id=report_id, updated_at=now(), **inherited))
        from .usage import release_budget
        reservation = (report.business_context or {}).get("budget_reservation_id")
        if reservation:
            release_budget(org_id=report.org_id, user_id=report.user_id or 0,
                system_id=report.system_id, reservation_id=reservation, transaction=db)
        db.add(report)
        from app.services.audit import record_audit_event
        record_audit_event(db, org_id=report.org_id, system_id=report.system_id,
            event_type="diagnosis.cancelled" if cancelled else "diagnosis.failed",
            actor_type="platform", target_type="diagnosis", target_id=str(report.id),
            status=report.status, output={"reason": reason, "execution_started": False}, commit=False)
    from .performance import pending_finished
    pending_finished(db,report_id)
    return True


def cancel_pending(report_id):
    with db_session() as db:
        result = finish_pending(db, report_id, cancelled=True, reason="已取消排队，任务未执行，预算预留已释放")
        db.commit()
        return result


def claim_job(report_id):
    with db_session() as db:
        job = db.get(DiagnosisJob, report_id)
        report = db.get(DiagnosisReport, report_id)
        if job is None or job.state != "pending" or report is None:
            return None
        if report.status != "running":
            db.execute(update(DiagnosisJob).where(DiagnosisJob.report_id == report_id,
                DiagnosisJob.state == "pending").values(state="done", payload={}, finished_at=now()))
            db.commit()
            return None
        if report.cancel_requested_at or aware(job.expires_at) <= now():
            finish_pending(db, report_id, cancelled=bool(report.cancel_requested_at))
            db.commit()
            return None
        payload = dict(job.payload)
        claimed = db.execute(update(DiagnosisJob).where(DiagnosisJob.report_id == report_id,
            DiagnosisJob.state == "pending").values(state="started", started_at=now()))
        if claimed.rowcount != 1:
            db.rollback()
            return None
        db.commit()
        return report.system_id, report.org_id, payload


def execute_job(report_id):
    claimed = claim_job(report_id)
    if claimed is None:
        return
    system_id, org_id, payload = claimed
    from .performance import create
    from app.agent.harness.performance import CURRENT
    performance=create(report_id)
    performance_binding=CURRENT.set(performance)
    context_span=performance.begin('context') if performance else None
    options = payload["options"]
    if payload.get("images"):
        options["images"] = [(mime, base64.b64decode(data)) for mime, data in payload["images"]]
    from .service import complete_diagnosis_report
    def enforce_deadline():
        from . import cancel_bus
        # Local native cancellation must still work if the database is down.
        cancel_bus.request_local_cancel(report_id)
        try:
            with db_session() as db:
                job = db.get(DiagnosisJob, report_id)
                report = db.get(DiagnosisReport, report_id)
                if job and job.state == "started" and report and report.status == "running":
                    _overdue(db, job, report)
                    db.commit()
        except Exception:
            log.warning("[diagnosis-deadline] report=%s check unavailable", report_id)
    timers = [threading.Timer(seconds, enforce_deadline) for seconds in
              (RUN_SECONDS + .05, RUN_SECONDS + STOP_GRACE_SECONDS + .05)]
    for timer in timers:
        timer.daemon = True
        timer.start()
    try:
        if options.get("resume_payload"):
            # A checkpoint can wait up to 15 minutes. Recompute evidence age
            # when execution starts, rather than reusing admission-time TTLs.
            from .resume import prepare_resume
            with db_session() as db:
                report = db.get(DiagnosisReport, report_id)
                source = db.get(DiagnosisReport, report.resumed_from_report_id)
                options["resume_payload"] = prepare_resume(db, source,
                    force_refresh=bool((report.business_context or {}).get("resume", {}).get("force_refresh")))
        complete_diagnosis_report(report_id, system_id, org_id, payload["question"], **options)
    finally:
        if performance: performance.finish()
        CURRENT.reset(performance_binding)
        for timer in timers:
            timer.cancel()
        # A crash leaves started state for reconciliation, never re-admission.
        with db_session() as db:
            report = db.get(DiagnosisReport, report_id)
            if report is not None and report.status != "running":
                db.execute(update(DiagnosisJob).where(DiagnosisJob.report_id == report_id).values(
                    state="done", payload={}, finished_at=now()))
                db.commit()


def _overdue(db, job, report):
    from . import cancel_bus
    elapsed = (now() - aware(job.started_at)).total_seconds()
    if elapsed <= RUN_SECONDS:
        return
    cancel_bus.set_cancel_requested(report.id)
    cancel_bus.publish_cancel(report.id)
    cancel_bus.request_local_cancel(report.id)
    db.refresh(report)
    if report.status != "running":
        return
    context = {**(report.business_context or {}), "execution_stop_reason": "run_timeout"}
    db.execute(update(DiagnosisReport).where(DiagnosisReport.id == report.id,
        DiagnosisReport.status == "running").values(business_context=context)
        .execution_options(synchronize_session=False))
    if elapsed <= RUN_SECONDS + STOP_GRACE_SECONDS:
        return
    # Fence the late runner's final write and conservatively settle unknown usage.
    from .termination import mark_execution_unknown
    from .usage import record_run_usage, settle_budget
    from app.models.usage import DiagnosisBudgetReservation
    db.refresh(report)
    if not mark_execution_unknown(db, report, "run_timeout"):
        return
    reservation_id = (report.business_context or {}).get("budget_reservation_id", "")
    summary = record_run_usage(report_id=report.id, org_id=report.org_id, system_id=report.system_id,
        usage_json='{"usage_complete":false,"cost_complete":false,"gap_reason":"run_timeout"}',
        before_json="", session=db, commit=False)
    reservation = db.get(DiagnosisBudgetReservation, reservation_id) if reservation_id else None
    if reservation:
        settle_budget(org_id=report.org_id, user_id=report.user_id or 0, system_id=report.system_id,
            reservation_id=reservation_id, tokens=max(summary["settle_tokens"], reservation.reserved_tokens),
            cost_usd=max(summary["settle_cost"], reservation.reserved_cost), session=db)


def dispatch_pending(publish=None):
    """Called periodically by API; broker loss cannot lose the persisted job.

    Send-after-commit intentionally permits duplicate messages. CAS admission
    prevents duplicate execution; lost publish reservations expire in 10 seconds.
    """
    from .performance import cleanup
    cleanup()
    if publish is None:
        from app.workers.celery import celery
        publish = lambda report_id: celery.send_task("app.tasks.diagnosis", args=[report_id],
            queue=QUEUE, retry=False, ignore_result=True)
    broker_available = True
    for state in ("started", "pending"):
        with db_session() as db:
            ids = db.exec(select(DiagnosisJob.report_id).where(DiagnosisJob.state == state)
                .order_by(DiagnosisJob.publish_after, DiagnosisJob.report_id).limit(100)).all()
        for report_id in ids:
            try:
                should_publish = False
                with db_session() as db:
                    job = db.get(DiagnosisJob, report_id)
                    report = db.get(DiagnosisReport, report_id)
                    if job is None or job.state != state:
                        continue
                    if report is None or report.status != "running":
                        db.execute(update(DiagnosisJob).where(DiagnosisJob.report_id == report_id).values(
                            state="done", payload={}, finished_at=now()))
                    elif state == "started":
                        if job.started_at:
                            _overdue(db, job, report)
                    elif aware(job.expires_at) <= now() or report.cancel_requested_at:
                        finish_pending(db, report_id, cancelled=bool(report.cancel_requested_at))
                    elif broker_available and aware(job.publish_after) <= now():
                        result = db.execute(update(DiagnosisJob).where(DiagnosisJob.report_id == report_id,
                            DiagnosisJob.state == "pending", DiagnosisJob.publish_after <= now()).values(
                            publish_after=now() + timedelta(seconds=10), attempts=DiagnosisJob.attempts + 1).execution_options(synchronize_session=False))
                        should_publish = result.rowcount == 1
                    db.commit()
                if should_publish:
                    try:
                        publish(report_id)
                    except Exception:
                        broker_available = False
                        raise
            except Exception as exc:
                # Never log the persisted prompt, credentials or image content.
                log.warning("[diagnosis-outbox] report=%s error=%s", report_id, type(exc).__name__)

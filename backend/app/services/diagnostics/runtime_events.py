"""Persist before advancing the consumer cursor; duplicate delivery is harmless."""
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models.runtime_events import DiagnosisRuntimeEvent
from .session import session as db_session


def persist_event(event, org_id, system_id):
    with db_session() as session:
        dialect=session.get_bind().dialect.name
        insert=sqlite_insert if dialect=='sqlite' else pg_insert
        statement=insert(DiagnosisRuntimeEvent).values(run_id=event.run_id,sequence=event.sequence,
            org_id=org_id,system_id=system_id,kind=event.kind,payload=event.payload)
        session.execute(statement.on_conflict_do_nothing(index_elements=['run_id','sequence']))
        session.commit()


def replay_events(session, run_id, org_id, system_id, after_sequence=0):
    from sqlmodel import select
    return session.exec(select(DiagnosisRuntimeEvent).where(DiagnosisRuntimeEvent.run_id==run_id,
        DiagnosisRuntimeEvent.org_id==org_id,DiagnosisRuntimeEvent.system_id==system_id,
        DiagnosisRuntimeEvent.sequence>after_sequence).order_by(DiagnosisRuntimeEvent.sequence).limit(100)).all()


def reconcile_accounting(limit=50):
    from sqlmodel import select
    from app.models.diagnostics import DiagnosisReport
    from app.models.run_state import DiagnosisRunState
    from app.agent.runtime.contracts import RuntimeResult
    from .service import _record_usage_and_settle
    completed=0
    with db_session() as db:
        reports=db.exec(select(DiagnosisReport).where(DiagnosisReport.usage_gap_reason.in_(['accounting_unavailable','execution_state_unknown'])).limit(limit)).all()
        jobs=[]
        for report in reports:
            state=db.get(DiagnosisRunState,report.id)
            uncertain=report.usage_gap_reason=='execution_state_unknown'
            if uncertain:
                from datetime import datetime, timezone, timedelta
                from app.core.config import settings
                from .cancel_bus import get as active_registration
                heartbeat=report.runner_heartbeat_at or report.created_at
                heartbeat=heartbeat.replace(tzinfo=timezone.utc) if heartbeat.tzinfo is None else heartbeat
                if active_registration(report.id) is not None or heartbeat>datetime.now(timezone.utc)-timedelta(seconds=settings.diagnosis_stale_heartbeat_seconds):
                    continue
            if (state is None or not state.framework_cumulative_json) and not uncertain:
                continue
            baseline=''
            if report.resumed_from_report_id:
                source=db.get(DiagnosisRunState,report.resumed_from_report_id)
                baseline=source.framework_cumulative_json if source else ''
            raw=state.framework_cumulative_json if state else ''
            if uncertain:
                import json
                try:
                    usage=json.loads(raw or '{}')
                    if not isinstance(usage,dict):usage={}
                except (ValueError,TypeError):usage={}
                usage.update(usage_complete=False,cost_complete=False,attempt_usage_complete=False,
                             gap_reason='execution_state_unknown')
                raw=json.dumps(usage)
            jobs.append((report.id,report.org_id,report.system_id,str(report.user_id or ''),raw,state.conversation_id if state else '',baseline))
    for rid,org,sid,actor,raw,conversation,baseline in jobs:
        _record_usage_and_settle(report_id=rid,org_id=org,system_id=sid,actor_id=actor,
            result=RuntimeResult('','',0,usage_json=raw,conversation_id=conversation),cancelled=False,cancel_phase='idle',baseline_json=baseline)
        completed+=1
    return completed

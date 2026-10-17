"""Lost-owner/grace-expiry policy shared by both diagnostic engines."""
from sqlalchemy import String, cast, update
from app.models.diagnostics import DiagnosisReport
from app.models.run_state import DiagnosisRunState


def mark_execution_unknown(session, report, reason, *, check_heartbeat=False, heartbeat_token=None):
    conditions=[DiagnosisReport.id==report.id,DiagnosisReport.status=='running',
                DiagnosisReport.runner_id==report.runner_id]
    if check_heartbeat:
        if heartbeat_token is not None:
            # Compare the exact DB representation captured with the report row.
            # Legacy SQLite raw-SQL timestamps may contain timezone suffixes
            # that ORM datetime binding removes; datetime equality misses them.
            conditions.append(cast(DiagnosisReport.runner_heartbeat_at,String)==heartbeat_token)
        else:
            conditions.append(DiagnosisReport.runner_heartbeat_at==report.runner_heartbeat_at
                              if report.runner_heartbeat_at is not None else DiagnosisReport.runner_heartbeat_at.is_(None))
    context=dict(report.business_context or {})
    context['execution_stop_unconfirmed']=True
    context['execution_stop_reason']=reason
    claimed=session.execute(update(DiagnosisReport).where(*conditions).values(
        status='failed',error_message=('执行状态未知：运行已超过 10 分钟，取消后仍未确认停止' if reason == 'run_timeout' else '执行状态未知：执行方已失联或未确认停止') + '；可能已产生费用，不会自动重跑。请核对后手动重新发起。',
        usage_complete=False,usage_gap_reason='execution_state_unknown',
        business_context=context,answer=report.answer or '执行停止未确认；不可继续，不自动重跑',
    ).execution_options(synchronize_session=False))
    if claimed.rowcount!=1:
        return False
    state=session.get(DiagnosisRunState,report.id)
    if state is None:
        state=DiagnosisRunState(report_id=report.id,messages_json='',resumable=False,
            engine=context.get('runtime_engine') or 'pydantic')
    state.resumable=False
    session.add(state)
    from app.services.audit import record_audit_event
    record_audit_event(session,org_id=report.org_id,system_id=report.system_id,
        event_type='diagnosis.failed',actor_type='platform',target_type='diagnosis',
        target_id=str(report.id),status='failed',
        output={'status':'failed','execution_stop_unconfirmed':True,'reason':reason})
    return True

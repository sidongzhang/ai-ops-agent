"""Scoped checkpoint evidence collection. Never treat display drafts as observations."""
from app.models.diagnostics import DiagnosisReport


def verification_calls(session, report_id, org_id, system_id, current_calls):
    inherited = []
    visited = set()
    report = session.get(DiagnosisReport, report_id) if report_id else None
    while report and report.resumed_from_report_id and len(visited) < 32:
        source_id = report.resumed_from_report_id
        if source_id in visited: break
        visited.add(source_id)
        report = session.get(DiagnosisReport, source_id)
        if not report or report.org_id != org_id or report.system_id != system_id: break
        inherited[0:0] = list(report.tool_calls or [])
    by_id = {}
    for call in inherited + list(current_calls or []):
        if not isinstance(call, dict) or not call.get('call_id'): continue
        previous = by_id.get(call['call_id'], {})
        by_id[call['call_id']] = {**previous, **call}
        if 'health_observation' in previous and 'health_observation' not in call:
            by_id[call['call_id']]['health_observation'] = previous['health_observation']
    return list(by_id.values())

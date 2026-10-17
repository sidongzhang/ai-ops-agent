"""Persistence/read policy for optional performance telemetry."""
import logging
import threading
import time
from datetime import datetime,timedelta,timezone
from sqlalchemy import update
from .session import session as db_session
from app.models.diagnosis_performance import DiagnosisPerformance
from app.models.diagnosis_jobs import DiagnosisJob
from app.models.diagnostics import DiagnosisReport
from app.agent.harness.performance import Recorder

log=logging.getLogger(__name__)
_last_cleanup=0
_cleanup_lock=threading.Lock()

def aware(value): return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value

def create(report_id):
    try:
        with db_session() as db:
            job=db.get(DiagnosisJob,report_id)
            queue_ms=max(0,(aware(job.started_at)-aware(job.created_at)).total_seconds()*1000) if job and job.started_at else None
        expires=datetime.now(timezone.utc)+timedelta(days=7)
        def persist(summary,detail):
            with db_session() as db:
                report=db.get(DiagnosisReport,report_id)
                if not report: return
                row=db.get(DiagnosisPerformance,report_id)
                if not row: row=DiagnosisPerformance(report_id=report_id,expires_at=expires)
                row.summary=summary
                row.detail=detail if aware(row.expires_at)>datetime.now(timezone.utc) else None
                db.add(row);db.commit()
        recorder=Recorder(persist,queue_ms=queue_ms)
        first=threading.Thread(target=recorder.flush,daemon=True)
        first.start();first.join(.1)
        return recorder
    except Exception:
        log.warning('Performance observer unavailable report=%s',report_id)
        return None

def read(db,report, *, detail=False):
    try:
        row=db.get(DiagnosisPerformance,report.id)
        if not row: return dict(state='not_collected',label='未采集')
        summary=dict(row.summary)
        summary['terminal_ready_ms']=summary.get('execution_ms')
        summary['final_report_available']=report.status=='success' and summary.get('report_ready_ms') is not None
        if report.status!='success': summary['report_ready_ms']=None
        detail_available=aware(row.expires_at)>datetime.now(timezone.utc) and row.detail is not None
        if summary.get('state')=='queued':
            job=db.get(DiagnosisJob,report.id)
            if job: summary['queue_ms']=max(0,(aware(job.started_at or job.finished_at or datetime.now(timezone.utc))-aware(job.created_at)).total_seconds()*1000)
        if report.status!='running' and summary.get('state') in ('collecting','queued'):
            summary['state']='incomplete'
            summary['counts_complete']=False
            summary['gaps']=[*summary.get('gaps',[]),'execution_ended_without_final_checkpoint']
        details=[dict(e) for e in row.detail or []] if detail_available and detail else None
        if summary['state']=='incomplete' and details:
            for event in details:
                if event['duration_ms'] is None: event['status']='unknown'
        return dict(**summary,detail_available=detail_available,detail_expires_at=aware(row.expires_at).isoformat(),timeline=details)
    except Exception:
        return dict(state='unavailable',label='性能采集暂不可用')

def cleanup():
    global _last_cleanup
    with _cleanup_lock:
        if time.monotonic()-_last_cleanup<3600: return
        try:
            with db_session() as db:
                db.execute(update(DiagnosisPerformance).where(DiagnosisPerformance.expires_at<datetime.now(timezone.utc),
                    DiagnosisPerformance.detail.is_not(None)).values(detail=None));db.commit()
            _last_cleanup=time.monotonic()
        except Exception: log.warning('Performance retention cleanup deferred')


def pending(db,report_id):
    try:
        with db.begin_nested():
            rec=Recorder(); summary,_=rec.snapshot();summary['state']='queued'
            db.add(DiagnosisPerformance(report_id=report_id,summary=summary,detail=[],expires_at=datetime.now(timezone.utc)+timedelta(days=7)))
            db.flush()
    except Exception: log.warning('Queue performance capture unavailable report=%s',report_id)

def pending_finished(db,report_id):
    try:
        with db.begin_nested():
            row=db.get(DiagnosisPerformance,report_id);job=db.get(DiagnosisJob,report_id)
            if row and job:
                summary=dict(row.summary);queue_ms=max(0,(aware(job.finished_at)-aware(job.created_at)).total_seconds()*1000)
                summary.update(state='complete',queue_ms=queue_ms,total_ms=queue_ms,execution_ms=0,report_ready_ms=None,execution_started=False)
                row.summary=summary;db.add(row);db.flush()
    except Exception: log.warning('Queue performance finalization unavailable report=%s',report_id)

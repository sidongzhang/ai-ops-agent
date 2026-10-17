"""Scoped chat CRUD; deletion purges content without refunding consumed budget."""
from datetime import datetime,timezone
from uuid import uuid4,uuid5,NAMESPACE_URL
from sqlmodel import Session,select
from sqlalchemy import delete,update
from app.models.chats import DiagnosisChat
from app.models.diagnostics import DiagnosisReport
from app.models.run_state import DiagnosisRunState
from app.models.runtime_events import DiagnosisRuntimeEvent
from app.models.knowledge import KnowledgeMemory
from app.models.audit import AuditLog
from app.models.systems import MonitoredSystem
from app.services.systems.service import require_system
from app.services.diagnostics.reports import _report_out

class ChatBusy(ValueError):pass


def lock_system(session,system_id,org_id):
    require_system(session,system_id,org_id)
    return session.exec(select(MonitoredSystem).where(MonitoredSystem.id==system_id,MonitoredSystem.org_id==org_id).with_for_update()).one()


def get_chat(session,system_id,org_id,chat_id):
    row=session.get(DiagnosisChat,chat_id)
    if not row or row.org_id!=org_id or row.system_id!=system_id or row.deleted:
        raise LookupError('对话不存在或已删除')
    return row


def create_chat(session,system_id,org_id,title='新对话'):
    lock_system(session,system_id,org_id)
    row=DiagnosisChat(id=uuid4().hex,org_id=org_id,system_id=system_id,title=title.strip() or '新对话')
    session.add(row);session.commit();session.refresh(row)
    return row


def _reports(session,system_id,org_id,chat_id):
    return session.exec(select(DiagnosisReport).where(DiagnosisReport.org_id==org_id,
        DiagnosisReport.system_id==system_id,DiagnosisReport.business_context['chat_id'].as_string()==chat_id)
        .order_by(DiagnosisReport.created_at,DiagnosisReport.id)).all()


def backfill_legacy(session,system_id,org_id):
    # Old clients did not send a UI chat ID. Preserve every report; group known resumes.
    lock_system(session,system_id,org_id)
    rows=session.exec(select(DiagnosisReport).where(DiagnosisReport.org_id==org_id,DiagnosisReport.system_id==system_id)).all()
    by_id={r.id:r for r in rows}
    for row in rows:
        if (row.business_context or {}).get('chat_id'):continue
        root=row;seen=set()
        while root.resumed_from_report_id in by_id and root.id not in seen:
            seen.add(root.id);root=by_id[root.resumed_from_report_id]
        chat_id=(root.business_context or {}).get('chat_id') or uuid5(NAMESPACE_URL,f'aiops-chat:{org_id}:{system_id}:{root.id}').hex
        chat=session.get(DiagnosisChat,chat_id)
        if chat is None:
            chat=DiagnosisChat(id=chat_id,org_id=org_id,system_id=system_id,title=(root.question or '图片诊断')[:120],created_at=root.created_at,updated_at=row.created_at)
            session.add(chat);session.flush()
        row.business_context={**(row.business_context or {}),'chat_id':chat_id};session.add(row)
    session.commit()


def list_chats(session,system_id,org_id):
    backfill_legacy(session,system_id,org_id)
    chats=session.exec(select(DiagnosisChat).where(DiagnosisChat.org_id==org_id,DiagnosisChat.system_id==system_id,DiagnosisChat.deleted==False).order_by(DiagnosisChat.updated_at.desc())).all()
    rows=session.exec(select(DiagnosisReport).where(DiagnosisReport.org_id==org_id,DiagnosisReport.system_id==system_id)).all()
    groups={}
    for row in rows:groups.setdefault((row.business_context or {}).get('chat_id'),[]).append(row)
    return [{**chat.model_dump(),'report_count':len(groups.get(chat.id,[])),
        'running':any(r.status=='running' for r in groups.get(chat.id,[]))} for chat in chats]


def chat_reports(session,system_id,org_id,chat_id):
    require_system(session,system_id,org_id);get_chat(session,system_id,org_id,chat_id)
    from .run_state import resumable_of
    from .resume import RESUMABLE_STATUSES
    from .usage import session_usage
    result=[]
    for row in _reports(session,system_id,org_id,chat_id):
        resume=resumable_of(row.id,session=session)
        state=session.get(DiagnosisRunState,row.id)
        totals=session_usage(org_id,system_id,conversation_id=state.conversation_id,session=session) if state and state.conversation_id else None
        extra={'resumable':resume,'can_resume':bool(resume and row.status in RESUMABLE_STATUSES)}
        if totals:extra.update(conversation_tokens=totals['tokens'],conversation_cost_usd=totals['cost_usd'])
        result.append(_report_out(row, session).model_copy(update=extra))
    return result


def rename_chat(session,system_id,org_id,chat_id,title):
    lock_system(session,system_id,org_id)
    chat=get_chat(session,system_id,org_id,chat_id)
    title=title.strip()
    if not title or len(title)>120:raise ValueError('对话名称需为1到120个字符')
    chat.title=title;chat.updated_at=datetime.now(timezone.utc);session.add(chat);session.commit();session.refresh(chat)
    return chat


def attach_chat(session,system_id,org_id,chat_id,question=''):
    lock_system(session,system_id,org_id)
    chat=get_chat(session,system_id,org_id,chat_id)
    if chat.title=='新对话':chat.title=(question.strip() or '图片诊断')[:120]
    chat.updated_at=datetime.now(timezone.utc);session.add(chat)
    return chat


def delete_chat(session,system_id,org_id,chat_id):
    lock_system(session,system_id,org_id)
    chat=session.get(DiagnosisChat,chat_id)
    if not chat or chat.org_id!=org_id or chat.system_id!=system_id:raise LookupError('对话不存在')
    if chat.deleted:return {'deleted':0}
    rows=_reports(session,system_id,org_id,chat_id)
    if any(row.status=='running' for row in rows):raise ChatBusy('对话正在执行，请先停止并等待结束后删除')
    ids=[r.id for r in rows]
    states=session.exec(select(DiagnosisRunState).where(DiagnosisRunState.report_id.in_(ids))).all() if ids else []
    run_ids=[s.run_id for s in states if s.run_id]
    pi_ids=[state.run_id for state in states if state.engine=='pi' and state.run_id]
    if pi_ids:
        # Delete the terminal sidecar copy first. Failure leaves the DB chat retryable.
        from app.core.config import settings
        from urllib.parse import urlparse,quote
        import httpx
        url=urlparse(settings.pi_runtime_url)
        if url.scheme!='http' or url.hostname not in ('localhost','127.0.0.1','::1') or url.username or url.password or url.path not in ('','/') or len(settings.pi_service_secret)<32:
            raise ChatBusy('Pi历史清理服务未配置，对话尚未删除')
        try:
            with httpx.Client(timeout=5,follow_redirects=False) as client:
                for run_id in pi_ids:
                    response=client.delete(settings.pi_runtime_url.rstrip('/')+'/runs/'+quote(run_id,safe=''),
                        headers={'Authorization':f'Bearer {settings.pi_service_secret}'},params={'org_id':org_id,'system_id':system_id})
                    response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ChatBusy('Pi历史清理失败，对话尚未删除；请启动Pi服务后重试') from exc
    # Only application conversation content is purged; accounting retains amounts/attempts.
    if ids:
        from app.models.diagnosis_jobs import DiagnosisJob
        from app.models.diagnosis_performance import DiagnosisPerformance
        session.execute(delete(DiagnosisPerformance).where(DiagnosisPerformance.report_id.in_(ids)))
        session.execute(delete(DiagnosisJob).where(DiagnosisJob.report_id.in_(ids)))
        session.execute(delete(DiagnosisRunState).where(DiagnosisRunState.report_id.in_(ids)))
        session.execute(delete(KnowledgeMemory).where(KnowledgeMemory.org_id==org_id,KnowledgeMemory.system_id==system_id,KnowledgeMemory.diagnosis_report_id.in_(ids)))
        session.execute(update(AuditLog).where(AuditLog.org_id==org_id,AuditLog.system_id==system_id,
            AuditLog.target_type.in_(['diagnosis','diagnosis_report']),AuditLog.target_id.in_([str(i) for i in ids])).values(input={},output={}))
        if run_ids:session.execute(delete(DiagnosisRuntimeEvent).where(DiagnosisRuntimeEvent.org_id==org_id,DiagnosisRuntimeEvent.system_id==system_id,DiagnosisRuntimeEvent.run_id.in_(run_ids)))
        # Remove child resumes first for the legacy self-reference constraint.
        for row in reversed(rows):session.delete(row)
        session.flush()
    chat.title='';chat.deleted=True;chat.updated_at=datetime.now(timezone.utc)
    session.add(chat);session.commit()
    from .answer_stream import discard_cached
    discard_cached(ids)
    return {'deleted':len(ids)}

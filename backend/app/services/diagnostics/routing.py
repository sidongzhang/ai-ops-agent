"""Scope-aware routing before budget reservation; no model or retrieval calls."""
from sqlmodel import select
from app.models.diagnostics import DiagnosisReport
from app.agent.harness.chat_profile import resolve,MODES
from .chats import get_chat,lock_system

class NeedsDiagnosis(ValueError):
    def __init__(self,route):
        self.route=route
        super().__init__('本轮需要图片、业务上下文或实时取证；请确认切换诊断后发送。')

def decision(db,system_id,org_id,question,*,chat_id='',special=False):
    from app.services.systems.service import require_system
    require_system(db,system_id,org_id)
    # Legacy saved modes must never block or force new turns. Native resumes use their job snapshot.
    if chat_id:get_chat(db,system_id,org_id,chat_id)
    return resolve(question,'auto',special=special)

def set_mode(db,system_id,org_id,chat_id,mode):
    if mode not in MODES:raise ValueError('不支持的对话模式')
    lock_system(db,system_id,org_id)
    chat=get_chat(db,system_id,org_id,chat_id)
    # Compatibility endpoint for older clients: new requests always route automatically.
    chat.interaction_mode='auto';db.add(chat);db.commit();db.refresh(chat)
    return chat

def recent_messages(db,system_id,org_id,chat_id):
    if not chat_id:return []
    rows=db.exec(select(DiagnosisReport).where(DiagnosisReport.org_id==org_id,
        DiagnosisReport.system_id==system_id,DiagnosisReport.status=='success',
        DiagnosisReport.business_context['chat_id'].as_string()==chat_id)
        .order_by(DiagnosisReport.created_at.desc(),DiagnosisReport.id.desc()).limit(3)).all()
    messages=[]
    for row in reversed(rows):
        messages.extend([dict(role='user',content=row.question),dict(role='assistant',content=row.answer)])
    budget=4000;selected=[]
    for message in reversed(messages):
        if budget<=0:break
        content=message['content'][:budget];budget-=len(content)
        selected.append({**message,'content':content})
    return list(reversed(selected))

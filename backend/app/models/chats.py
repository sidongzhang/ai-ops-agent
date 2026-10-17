"""User-facing chats are distinct from SDK conversation/run identities."""
from datetime import datetime
from sqlmodel import SQLModel,Field
from .common import utcnow

class DiagnosisChat(SQLModel,table=True):
    __tablename__='diagnosis_chat'
    id:str=Field(primary_key=True,max_length=64)
    org_id:int=Field(index=True)
    system_id:int=Field(index=True)
    title:str=Field(max_length=120)
    interaction_mode:str=Field(default="auto",max_length=16)
    # Content is hard-deleted. This empty marker only prevents stale clients recreating it.
    deleted:bool=False
    created_at:datetime=Field(default_factory=utcnow)
    updated_at:datetime=Field(default_factory=utcnow)

"""Authenticated chat management for console and the existing system-token gateway."""
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from typing import Literal
from sqlmodel import Session
from app.core.database import get_session
from app.core.deps import get_current_org_id,get_current_user
from app.services.diagnostics import chats

class ChatTitle(BaseModel):
    title:str=Field(default='新对话',min_length=1,max_length=120)


def call(function,*args):
    try:return function(*args)
    except chats.ChatBusy as exc:raise HTTPException(409,str(exc))
    except LookupError as exc:raise HTTPException(404,str(exc))
    except ValueError as exc:raise HTTPException(400,str(exc))

router=APIRouter(prefix='/systems',tags=['agent'],dependencies=[Depends(get_current_user)])

@router.get('/{system_id}/diagnosis-chats')
def list_chats(system_id:int,session:Session=Depends(get_session),org_id:int=Depends(get_current_org_id)):
    return call(chats.list_chats,session,system_id,org_id)

@router.post('/{system_id}/diagnosis-chats',status_code=201)
def create_chat(system_id:int,body:ChatTitle,session:Session=Depends(get_session),org_id:int=Depends(get_current_org_id)):
    return call(chats.create_chat,session,system_id,org_id,body.title)

@router.get('/{system_id}/diagnosis-chats/{chat_id}/reports')
def reports(system_id:int,chat_id:str,session:Session=Depends(get_session),org_id:int=Depends(get_current_org_id)):
    return call(chats.chat_reports,session,system_id,org_id,chat_id)

@router.patch('/{system_id}/diagnosis-chats/{chat_id}')
def rename_chat(system_id:int,chat_id:str,body:ChatTitle,session:Session=Depends(get_session),org_id:int=Depends(get_current_org_id)):
    return call(chats.rename_chat,session,system_id,org_id,chat_id,body.title)

@router.delete('/{system_id}/diagnosis-chats/{chat_id}')
def delete_chat(system_id:int,chat_id:str,session:Session=Depends(get_session),org_id:int=Depends(get_current_org_id)):
    return call(chats.delete_chat,session,system_id,org_id,chat_id)


class ChatMode(BaseModel):
    interaction_mode:Literal['auto','chat','diagnosis']='auto'

class RoutePreview(BaseModel):
    question:str=Field(default='',max_length=20000)
    chat_id:str=Field(default='',max_length=64)
    has_images:bool=False
    has_context:bool=False

@router.post('/{system_id}/diagnosis-route')
def preview(system_id:int,body:RoutePreview,session:Session=Depends(get_session),org_id:int=Depends(get_current_org_id)):
    from app.services.diagnostics.routing import decision
    return call(lambda:decision(session,system_id,org_id,body.question,chat_id=body.chat_id,special=body.has_images or body.has_context))

@router.post('/{system_id}/diagnosis-chats/{chat_id}/mode')
def change_mode(system_id:int,chat_id:str,body:ChatMode,session:Session=Depends(get_session),org_id:int=Depends(get_current_org_id)):
    from app.services.diagnostics.routing import set_mode
    return call(set_mode,session,system_id,org_id,chat_id,body.interaction_mode)

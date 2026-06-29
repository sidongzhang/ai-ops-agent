"""系统注册 CRUD。所有操作严格按当前用户的 org 隔离。"""
import httpx

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from ..core.database import get_session
from ..core.deps import get_current_org_id
from ..schemas import NotifyConfig, ServiceIn, ServiceOut, SystemCreate, SystemOut
from ..services.notifications.config import send_test_notification
from ..services.systems.service import (
    add_service as add_service_record,
    create_system as create_system_record,
    delete_service as delete_service_record,
    get_decrypted_notify,
    get_system as get_system_detail,
    list_systems as list_system_records,
    update_notify as update_notify_config,
)

router = APIRouter(prefix="/systems", tags=["systems"])


@router.post("", response_model=SystemOut, status_code=status.HTTP_201_CREATED)
def create_system(body: SystemCreate, session: Session = Depends(get_session),
                  org_id: int = Depends(get_current_org_id)):
    try:
        return create_system_record(session, org_id, body)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("", response_model=list[SystemOut])
def list_systems(session: Session = Depends(get_session),
                 org_id: int = Depends(get_current_org_id)):
    return list_system_records(session, org_id)


@router.get("/{system_id}", response_model=SystemOut)
def get_system(system_id: int, session: Session = Depends(get_session),
               org_id: int = Depends(get_current_org_id)):
    try:
        return get_system_detail(session, system_id, org_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.post("/{system_id}/services", response_model=ServiceOut, status_code=status.HTTP_201_CREATED)
def add_service(system_id: int, body: ServiceIn,
                session: Session = Depends(get_session),
                org_id: int = Depends(get_current_org_id)):
    try:
        return add_service_record(session, system_id, org_id, body)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.delete("/{system_id}/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(system_id: int, service_id: int,
                   session: Session = Depends(get_session),
                   org_id: int = Depends(get_current_org_id)):
    try:
        delete_service_record(session, system_id, service_id, org_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.put("/{system_id}/notify", response_model=dict)
def update_notify(system_id: int, body: NotifyConfig,
                  session: Session = Depends(get_session),
                  org_id: int = Depends(get_current_org_id)):
    try:
        return update_notify_config(session, system_id, org_id, body)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.post("/{system_id}/notify/test", status_code=status.HTTP_200_OK)
def test_notify(system_id: int,
                session: Session = Depends(get_session),
                org_id: int = Depends(get_current_org_id)):
    try:
        system_name, cfg = get_decrypted_notify(session, system_id, org_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))

    try:
        send_test_notification(system_name, cfg)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except httpx.RequestError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"请求失败: {exc}")
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"发送失败: {exc}")
    return {"ok": True, "message": "测试通知已发送"}

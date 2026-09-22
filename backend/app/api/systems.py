"""系统注册 CRUD。所有操作严格按当前用户的 org 隔离。"""
import httpx

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from ..core.database import get_session
from ..core.deps import get_current_org_id, get_current_user, require_operator
from ..models.auth import User
from ..schemas import (
    NotifyConfig,
    MonitoringConfig,
    RestartPolicyOut,
    RestartExecuteIn,
    RestartExecuteOut,
    RestartPolicyUpdate,
    ServiceIn,
    ServiceOut,
    ServiceProbeOut,
    SystemCreate,
    SystemOut,
)
from ..services.notifications.config import send_test_notification
from ..services.systems.service import (
    add_service as add_service_record,
    create_system as create_system_record,
    delete_system as delete_system_record,
    delete_service as delete_service_record,
    enable_service_draft as enable_service_draft_record,
    execute_registered_service_restart as execute_registered_service_restart_record,
    get_decrypted_notify,
    get_system as get_system_detail,
    list_systems as list_system_records,
    test_service_draft as test_service_draft_record,
    get_restart_policy as get_restart_policy_record,
    update_service_draft as update_service_draft_record,
    update_restart_policy as update_restart_policy_config,
    update_notify as update_notify_config,
    get_monitoring_config,
    require_system,
    update_monitoring_config,
)

router = APIRouter(prefix="/systems", tags=["systems"])


@router.post("", response_model=SystemOut, status_code=status.HTTP_201_CREATED)
def create_system(body: SystemCreate, session: Session = Depends(get_session),
                  org_id: int = Depends(get_current_org_id),
                  user: User = Depends(require_operator)):
    try:
        return create_system_record(
            session,
            org_id,
            body,
            creator_user_id=user.id,
            current_user=user,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("", response_model=list[SystemOut])
def list_systems(session: Session = Depends(get_session),
                 org_id: int = Depends(get_current_org_id),
                 user: User = Depends(get_current_user)):
    return list_system_records(session, org_id, user)


@router.get("/{system_id}", response_model=SystemOut)
def get_system(system_id: int, session: Session = Depends(get_session),
               org_id: int = Depends(get_current_org_id),
               user: User = Depends(get_current_user)):
    try:
        return get_system_detail(session, system_id, org_id, user)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.delete("/{system_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_system(system_id: int,
                  session: Session = Depends(get_session),
                  org_id: int = Depends(get_current_org_id),
                  user: User = Depends(require_operator)):
    try:
        delete_system_record(session, system_id, org_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.post("/{system_id}/services", response_model=ServiceOut, status_code=status.HTTP_201_CREATED)
def add_service(system_id: int, body: ServiceIn,
                session: Session = Depends(get_session),
                org_id: int = Depends(get_current_org_id),
                user: User = Depends(require_operator)):
    try:
        return add_service_record(session, system_id, org_id, body, actor_id=str(user.id))
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.put("/{system_id}/services/{service_id}", response_model=ServiceOut)
def update_service_draft(
    system_id: int,
    service_id: int,
    body: ServiceIn,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(require_operator),
):
    try:
        return update_service_draft_record(
            session, system_id, service_id, org_id, body, actor_id=str(user.id)
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.post("/{system_id}/services/{service_id}/test", response_model=ServiceProbeOut)
async def test_service_draft(
    system_id: int,
    service_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(require_operator),
):
    try:
        return await test_service_draft_record(
            session, system_id, service_id, org_id, actor_id=str(user.id)
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.post("/{system_id}/services/{service_id}/enable", response_model=ServiceOut)
def enable_service_draft(
    system_id: int,
    service_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(require_operator),
):
    try:
        return enable_service_draft_record(
            session, system_id, service_id, org_id, actor_id=str(user.id)
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.delete("/{system_id}/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(system_id: int, service_id: int,
                   session: Session = Depends(get_session),
                   org_id: int = Depends(get_current_org_id),
                   user: User = Depends(require_operator)):
    try:
        delete_service_record(
            session, system_id, service_id, org_id, actor_id=str(user.id)
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.post("/{system_id}/services/restart", response_model=RestartExecuteOut)
async def restart_service(
    system_id: int,
    body: RestartExecuteIn,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(require_operator),
):
    try:
        return await execute_registered_service_restart_record(
            session,
            system_id,
            org_id,
            body.service,
            user,
            actor_id=str(user.id),
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.put("/{system_id}/notify", response_model=dict)
def update_notify(system_id: int, body: NotifyConfig,
                  session: Session = Depends(get_session),
                  org_id: int = Depends(get_current_org_id),
                  user: User = Depends(require_operator)):
    try:
        return update_notify_config(session, system_id, org_id, body)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.get("/{system_id}/monitoring", response_model=MonitoringConfig)
def read_monitoring(
    system_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    try:
        return get_monitoring_config(require_system(session, system_id, org_id))
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.put("/{system_id}/monitoring", response_model=MonitoringConfig)
def write_monitoring(
    system_id: int,
    body: MonitoringConfig,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(require_operator),
):
    try:
        return update_monitoring_config(session, system_id, org_id, body, actor_id=str(user.id))
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.post("/{system_id}/notify/test", status_code=status.HTTP_200_OK)
def test_notify(system_id: int,
                session: Session = Depends(get_session),
                org_id: int = Depends(get_current_org_id),
                user: User = Depends(require_operator)):
    try:
        system_name, cfg = get_decrypted_notify(session, system_id, org_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))

    try:
        results = send_test_notification(system_name, cfg)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except httpx.RequestError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"请求失败: {exc}")
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"发送失败: {exc}")
    return {"ok": True, "message": "测试通知已发送", "channels": results}



@router.get("/{system_id}/restart-policy", response_model=RestartPolicyOut)
def read_restart_policy(
    system_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(get_current_user),
):
    try:
        return get_restart_policy_record(session, system_id, org_id, user)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))

@router.put("/{system_id}/restart-policy", response_model=RestartPolicyOut)
def update_restart_policy(
    system_id: int,
    body: RestartPolicyUpdate,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(get_current_user),
):
    try:
        return update_restart_policy_config(session, system_id, org_id, user, body)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

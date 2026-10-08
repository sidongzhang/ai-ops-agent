"""系统级配置：巡检周期、通知渠道、重启授权。

从 systems/service.py 拆出——这三类配置都是「读写 system.infra/notify/restart_policy」，
与系统/服务的增删改无关。
"""
from sqlmodel import Session, select

from app.core.security import (
    decrypt_sensitive_fields,
    encrypt_sensitive_fields,
    mask_sensitive_fields,
)
from app.models.auth import User
from app.models.systems import MonitoredSystem
from app.schemas import MonitoringConfig, NotifyConfig, RestartPolicyOut, RestartPolicyUpdate
from app.services.audit import record_audit_event
from app.services.notifications.config import merge_notify_config
from app.services.systems.access import require_system
from app.services.systems.restart import (
    can_manage_restart_policy,
    has_restart_permission,
    normalize_restart_policy,
)


def get_monitoring_config(system: MonitoredSystem) -> MonitoringConfig:
    infra = decrypt_sensitive_fields(system.infra or {})
    raw = infra.get("monitoring") or {}
    try:
        interval = int(raw.get("interval_seconds", 60) or 60)
    except (TypeError, ValueError):
        interval = 60
    return MonitoringConfig(
        enabled=bool(raw.get("enabled", True)),
        interval_seconds=max(15, min(interval, 86400)),
    )


def update_monitoring_config(
    session: Session,
    system_id: int,
    org_id: int,
    body: MonitoringConfig,
    *,
    actor_id: str = "",
) -> MonitoringConfig:
    system = require_system(session, system_id, org_id)
    infra = decrypt_sensitive_fields(system.infra or {})
    infra["monitoring"] = body.model_dump()
    system.infra = encrypt_sensitive_fields(infra)
    session.add(system)
    record_audit_event(
        session,
        org_id=org_id,
        system_id=system.id,
        event_type="monitoring.config_updated",
        actor_type="user",
        actor_id=actor_id,
        target_type="system",
        target_id=str(system.id),
        input=body.model_dump(),
    )
    session.commit()
    return body


def update_notify(session: Session, system_id: int, org_id: int, body: NotifyConfig) -> dict:
    system = require_system(session, system_id, org_id)
    merged = merge_notify_config(body, system.notify)
    system.notify = encrypt_sensitive_fields(merged)
    session.add(system)
    session.commit()
    session.refresh(system)
    return mask_sensitive_fields(system.notify)


def get_decrypted_notify(session: Session, system_id: int, org_id: int) -> tuple[str, dict]:
    system = require_system(session, system_id, org_id)
    return system.name, decrypt_sensitive_fields(system.notify or {})


def get_restart_policy(
    session: Session,
    system_id: int,
    org_id: int,
    current_user: User,
) -> RestartPolicyOut:
    system = require_system(session, system_id, org_id)
    policy = normalize_restart_policy(system)
    return RestartPolicyOut(
        authorized_user_ids=policy["authorized_user_ids"],
        has_permission=has_restart_permission(current_user, system),
        can_manage=can_manage_restart_policy(current_user, system),
    )


def update_restart_policy(
    session: Session,
    system_id: int,
    org_id: int,
    current_user: User,
    body: RestartPolicyUpdate,
) -> RestartPolicyOut:
    system = require_system(session, system_id, org_id)
    if not can_manage_restart_policy(current_user, system):
        raise PermissionError("只有组织 owner 可以管理重启权限")

    allowed_ids = sorted({int(user_id) for user_id in body.authorized_user_ids if user_id > 0})
    if current_user.id not in allowed_ids:
        allowed_ids.insert(0, current_user.id)

    org_user_ids = {
        user_id
        for user_id in session.exec(select(User.id).where(User.org_id == org_id)).all()
    }
    invalid_ids = [user_id for user_id in allowed_ids if user_id not in org_user_ids]
    if invalid_ids:
        raise ValueError(f"用户 {invalid_ids} 不属于当前组织")

    system.restart_policy = {"authorized_user_ids": allowed_ids}
    session.add(system)
    session.commit()
    session.refresh(system)

    policy = normalize_restart_policy(system)
    return RestartPolicyOut(
        authorized_user_ids=policy["authorized_user_ids"],
        has_permission=True,
        can_manage=True,
    )

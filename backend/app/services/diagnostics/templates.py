"""诊断模板（Playbook）的查询与启停管理。

从 diagnostics/service.py 拆出：模板管理只关心「系统启用了哪些 Playbook」，
与诊断执行流程无关，独立成模块便于阅读与测试。
"""
from sqlmodel import Session

from app.agent.diagnostics.skill_router import list_skills
from app.core.security import decrypt_sensitive_fields, encrypt_sensitive_fields
from app.schemas import DiagnosticTemplateOut, DiagnosticTemplateSettingsUpdate
from app.services.audit import record_audit_event
from app.services.systems.service import require_system


def disabled_template_names(system) -> set[str]:
    infra = decrypt_sensitive_fields(system.infra or {})
    settings = infra.get("diagnostic_templates") or {}
    return {str(name) for name in settings.get("disabled_names", [])}


def list_diagnostic_templates(
    session: Session,
    system_id: int,
    org_id: int,
) -> list[DiagnosticTemplateOut]:
    system = require_system(session, system_id, org_id)
    disabled = disabled_template_names(system)
    return [
        DiagnosticTemplateOut(**skill, enabled=skill["name"] not in disabled)
        for skill in list_skills()
    ]


def update_diagnostic_templates(
    session: Session,
    system_id: int,
    org_id: int,
    body: DiagnosticTemplateSettingsUpdate,
    *,
    actor_id: str = "",
) -> list[DiagnosticTemplateOut]:
    system = require_system(session, system_id, org_id)
    valid_names = {skill["name"] for skill in list_skills()}
    disabled = sorted(set(body.disabled_names))
    unknown = [name for name in disabled if name not in valid_names]
    if unknown:
        raise ValueError(f"未知诊断模板：{', '.join(unknown)}")

    infra = decrypt_sensitive_fields(system.infra or {})
    infra["diagnostic_templates"] = {"disabled_names": disabled}
    system.infra = encrypt_sensitive_fields(infra)
    session.add(system)
    record_audit_event(
        session,
        org_id=org_id,
        system_id=system.id,
        event_type="diagnostic_templates.updated",
        actor_type="user",
        actor_id=actor_id,
        target_type="system",
        target_id=str(system.id),
        input={"disabled_names": disabled},
        output={"enabled_count": len(valid_names) - len(disabled)},
    )
    session.commit()
    return list_diagnostic_templates(session, system.id, org_id)


__all__ = ["disabled_template_names", "list_diagnostic_templates", "update_diagnostic_templates"]

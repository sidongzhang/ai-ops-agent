"""Application services for AI diagnosis and diagnostic playbooks."""
import time

from sqlmodel import Session

from app.agent.diagnostics.runner import diagnose_with_details
from app.agent.diagnostics.skill_router import list_skills, match_skill
from app.core.security import decrypt_sensitive_fields, encrypt_sensitive_fields
from app.repositories.systems import list_enabled_services_for_system
from app.schemas import (
    DiagnosticTemplateOut,
    DiagnosticTemplateSettingsUpdate,
    DiagnoseResponse,
)
from app.services.audit import record_audit_event
from app.services.data_analysis import analyze_system_data, is_data_analysis_question
from app.services.descriptors.builder import system_to_descriptor
from app.services.systems.service import require_system


def _disabled_template_names(system) -> set[str]:
    infra = decrypt_sensitive_fields(system.infra or {})
    settings = infra.get("diagnostic_templates") or {}
    return {str(name) for name in settings.get("disabled_names", [])}


def list_diagnostic_templates(
    session: Session,
    system_id: int,
    org_id: int,
) -> list[DiagnosticTemplateOut]:
    system = require_system(session, system_id, org_id)
    disabled = _disabled_template_names(system)
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


def _evidence_sources(steps: str, tool_calls: list[dict]) -> list[str]:
    combined = " ".join([steps, *(call.get("tool", "") for call in tool_calls)])
    source_map = (
        ("服务健康检查", ("check_service", "list_services")),
        ("Prometheus 指标", ("query_prometheus",)),
        ("服务日志", ("read_logs", "search_logs")),
        ("Redis 运行数据", ("run_redis_command",)),
        ("Kafka 运行数据", ("run_kafka_command",)),
        ("运维知识库", ("search_knowledge_base",)),
    )
    return [label for label, markers in source_map if any(marker in combined for marker in markers)]


def diagnose_system(
    session: Session,
    system_id: int,
    org_id: int,
    question: str,
    *,
    actor_id: str = "",
) -> DiagnoseResponse:
    system = require_system(session, system_id, org_id)
    started_at = time.monotonic()
    if is_data_analysis_question(question):
        try:
            analysis = analyze_system_data(
                session,
                system.id,
                org_id,
                question,
                actor_type="diagnose",
                actor_id="agent",
            )
            is_task_analysis = analysis.evidence.get("analysis_type") == "stuck_tasks"
            evidence_sources = ["只读数据库查询"]
            if is_task_analysis and analysis.evidence.get("worker_health"):
                evidence_sources.append("Worker 健康检查")
            if is_task_analysis and analysis.evidence.get("worker_logs"):
                evidence_sources.append("Worker 日志")
            return DiagnoseResponse(
                system_id=system.id,
                answer=analysis.answer,
                template_name="stuck_task_analysis" if is_task_analysis else "readonly_data_analysis",
                template_description="任务卡住专项分析" if is_task_analysis else "只读业务数据分析",
                duration_ms=round((time.monotonic() - started_at) * 1000),
                evidence_sources=evidence_sources,
            )
        except ValueError as exc:
            return DiagnoseResponse(
                system_id=system.id,
                answer=(
                    f"这个问题需要查询业务数据库，但当前无法执行只读分析：{exc}。\n"
                    "请先在系统 infra.readonly_database 中配置只读数据源、表名和时间字段；"
                    "配置后我可以统计数据是否到达、数量是否异常，并返回查询依据。"
                ),
            )
    template = match_skill(question, _disabled_template_names(system))
    descriptor = system_to_descriptor(system, list_enabled_services_for_system(session, system.id))
    try:
        run = diagnose_with_details(
            descriptor,
            question,
            org_id=org_id,
            system_id=system.id,
            skill_steps=template["steps"] if template else "",
        )
    except Exception as exc:
        record_audit_event(
            session,
            org_id=org_id,
            system_id=system.id,
            event_type="diagnosis.failed",
            actor_type="user",
            actor_id=actor_id,
            target_type="diagnosis",
            status="failed",
            input={"question": question, "template_name": template["name"] if template else ""},
            output={
                "error": str(exc),
                "duration_ms": round((time.monotonic() - started_at) * 1000),
            },
            commit=True,
        )
        raise

    evidence_sources = _evidence_sources(template["steps"] if template else "", run.tool_calls)
    record_audit_event(
        session,
        org_id=org_id,
        system_id=system.id,
        event_type="diagnosis.completed",
        actor_type="user",
        actor_id=actor_id,
        target_type="diagnosis",
        status="success",
        input={
            "question": question,
            "template_name": template["name"] if template else "",
            "template_description": template["description"] if template else "自由诊断",
        },
        output={
            "answer": run.answer,
            "model": run.model,
            "duration_ms": run.duration_ms,
            "total_tokens": run.total_tokens,
            "evidence_sources": evidence_sources,
            "tool_calls": run.tool_calls,
        },
        commit=True,
    )
    return DiagnoseResponse(
        system_id=system.id,
        answer=run.answer,
        template_name=template["name"] if template else "",
        template_description=template["description"] if template else "自由诊断",
        duration_ms=run.duration_ms,
        evidence_sources=evidence_sources,
    )

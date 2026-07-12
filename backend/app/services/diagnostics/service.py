"""Application services for AI diagnosis and diagnostic playbooks."""
import asyncio
import time

from sqlmodel import Session

from app.agent.diagnostics.runner import diagnose_with_details
from app.agent.diagnostics.skill_router import list_skills, match_skill
from app.agent.diagnostics.knowledge.store import get_relevant_context
from app.core.security import decrypt_sensitive_fields, encrypt_sensitive_fields
from app.repositories.systems import list_enabled_services_for_system
from app.services.collectors.exec import select_online_collector
from app.schemas import (
    DiagnosticTemplateOut,
    DiagnosticTemplateSettingsUpdate,
    DiagnoseResponse,
)
from app.services.audit import record_audit_event
from app.services.data_analysis import analyze_system_data, get_readonly_database_config, is_data_analysis_question
from app.services.descriptors.builder import system_to_descriptor
from app.services.diagnostics.evidence import (
    build_evidence_from_data_analysis,
    build_evidence_from_tool_calls,
    build_evidence_steps,
    build_knowledge_refs,
)
from app.services.diagnostics.reports import save_diagnosis_report
from app.services.systems.service import require_system
from app.services.realtime.websocket import manager


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


def _remote_command(session: Session, system):
    if system.local:
        return None
    collector = select_online_collector(session, system.id)
    if not collector or not manager.is_connected(collector.id):
        return None

    def execute(command: str, args: dict) -> dict:
        try:
            return asyncio.run(manager.send_command(collector.id, command, args))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "result": str(exc)}

    return execute


def _business_data_query(session: Session, system, org_id: int, actor_id: str):
    config = get_readonly_database_config(session, system.id, org_id)
    if not config.enabled:
        return None

    def query(question: str) -> str:
        try:
            result = analyze_system_data(
                session,
                system.id,
                org_id,
                question,
                actor_type="agent",
                actor_id=actor_id or "agent",
            )
            parts = [result.answer]
            evidence = result.evidence or {}
            if evidence.get("analysis_type") == "stuck_tasks":
                parts.append(
                    f"卡住任务数：{evidence.get('stuck_count', 0)}，"
                    f"阈值：{evidence.get('stuck_threshold_minutes', 0)} 分钟"
                )
            elif evidence.get("total") is not None:
                parts.append(f"统计总量：{evidence.get('total')}")
            return "\n".join(parts)
        except ValueError as exc:
            return f"无法查询业务数据：{exc}"

    return query


def _user_id_from_actor(actor_id: str) -> int | None:
    if not actor_id:
        return None
    try:
        return int(actor_id)
    except ValueError:
        return None


def _build_diagnose_response(
    *,
    report_id: int | None,
    system_id: int,
    answer: str,
    template_name: str = "",
    template_description: str = "",
    model: str = "",
    duration_ms: int = 0,
    total_tokens: int = 0,
    evidence_sources: list[str] | None = None,
    tool_calls: list[dict] | None = None,
    evidence: list[dict] | None = None,
    evidence_steps: list[str] | None = None,
    knowledge_refs: list[dict] | None = None,
) -> DiagnoseResponse:
    tool_calls = tool_calls or []
    evidence = evidence or build_evidence_from_tool_calls(tool_calls)
    evidence_steps = evidence_steps or build_evidence_steps(tool_calls)
    return DiagnoseResponse(
        id=report_id,
        system_id=system_id,
        answer=answer,
        template_name=template_name,
        template_description=template_description,
        model=model,
        duration_ms=duration_ms,
        total_tokens=total_tokens,
        evidence_sources=evidence_sources or [],
        evidence_steps=evidence_steps,
        tool_calls=tool_calls,
        evidence=evidence,
        knowledge_refs=knowledge_refs or [],
    )


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
    user_id = _user_id_from_actor(actor_id)
    if is_data_analysis_question(question):
        try:
            analysis = analyze_system_data(
                session,
                system.id,
                org_id,
                question,
                actor_type="diagnose",
                actor_id=actor_id or "agent",
            )
            is_task_analysis = analysis.evidence.get("analysis_type") == "stuck_tasks"
            evidence_sources = ["只读数据库查询"]
            if is_task_analysis and analysis.evidence.get("worker_health"):
                evidence_sources.append("Worker 健康检查")
            if is_task_analysis and analysis.evidence.get("worker_logs"):
                evidence_sources.append("Worker 日志")
            evidence_items, evidence_steps = build_evidence_from_data_analysis(analysis.evidence)
            duration_ms = round((time.monotonic() - started_at) * 1000)
            template_name = "stuck_task_analysis" if is_task_analysis else "readonly_data_analysis"
            template_description = "任务卡住专项分析" if is_task_analysis else "只读业务数据分析"
            report = save_diagnosis_report(
                session,
                org_id=org_id,
                system_id=system.id,
                user_id=user_id,
                report_type="data_analysis",
                question=question,
                answer=analysis.answer,
                template_name=template_name,
                template_description=template_description,
                duration_ms=duration_ms,
                evidence_sources=evidence_sources,
                evidence_steps=evidence_steps,
                evidence=evidence_items,
            )
            return _build_diagnose_response(
                report_id=report.id,
                system_id=system.id,
                answer=analysis.answer,
                template_name=template_name,
                template_description=template_description,
                duration_ms=duration_ms,
                evidence_sources=evidence_sources,
                evidence=evidence_items,
                evidence_steps=evidence_steps,
            )
        except ValueError as exc:
            answer = (
                f"这个问题需要查询业务数据库，但当前无法执行只读分析：{exc}。\n"
                "请联系管理员在后台配置只读业务数据源后，我才能统计业务数据是否到达、数量是否异常。"
            )
            report = save_diagnosis_report(
                session,
                org_id=org_id,
                system_id=system.id,
                user_id=user_id,
                report_type="data_analysis",
                question=question,
                answer=answer,
                status="failed",
                template_name="readonly_data_analysis",
                template_description="只读业务数据分析",
                duration_ms=round((time.monotonic() - started_at) * 1000),
                error_message=str(exc),
            )
            return _build_diagnose_response(
                report_id=report.id,
                system_id=system.id,
                answer=answer,
                template_name="readonly_data_analysis",
                template_description="只读业务数据分析",
                duration_ms=report.duration_ms,
            )
    template = match_skill(question, _disabled_template_names(system))
    descriptor = system_to_descriptor(system, list_enabled_services_for_system(session, system.id))
    knowledge_context = get_relevant_context(question, str(system.id))
    try:
        run = diagnose_with_details(
            descriptor,
            question,
            org_id=org_id,
            system_id=system.id,
            skill_steps=template["steps"] if template else "",
            knowledge_context=knowledge_context,
            remote_command=_remote_command(session, system),
            business_data_query=_business_data_query(session, system, org_id, actor_id),
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
        save_diagnosis_report(
            session,
            org_id=org_id,
            system_id=system.id,
            user_id=user_id,
            report_type="diagnose",
            question=question,
            answer="",
            status="failed",
            template_name=template["name"] if template else "",
            template_description=template["description"] if template else "自由诊断",
            duration_ms=round((time.monotonic() - started_at) * 1000),
            error_message=str(exc),
        )
        raise

    evidence_sources = _evidence_sources(template["steps"] if template else "", run.tool_calls)
    evidence_items = build_evidence_from_tool_calls(run.tool_calls)
    evidence_steps = build_evidence_steps(run.tool_calls)
    knowledge_refs = build_knowledge_refs(system.id, run.tool_calls, question=question)
    record_audit_event(
        session,
        org_id=org_id,
        system_id=system.id,
        event_type="diagnosis.completed",
        actor_type="user",
        actor_id=actor_id,
        target_type="diagnosis",
        target_id="",
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
        commit=False,
    )
    report = save_diagnosis_report(
        session,
        org_id=org_id,
        system_id=system.id,
        user_id=user_id,
        report_type="diagnose",
        question=question,
        answer=run.answer,
        template_name=template["name"] if template else "",
        template_description=template["description"] if template else "自由诊断",
        model=run.model,
        duration_ms=run.duration_ms,
        total_tokens=run.total_tokens,
        evidence_sources=evidence_sources,
        evidence_steps=evidence_steps,
        tool_calls=run.tool_calls,
        evidence=evidence_items,
        knowledge_refs=knowledge_refs,
        commit=True,
    )
    return _build_diagnose_response(
        report_id=report.id,
        system_id=system.id,
        answer=run.answer,
        template_name=report.template_name,
        template_description=report.template_description,
        model=run.model,
        duration_ms=run.duration_ms,
        total_tokens=run.total_tokens,
        evidence_sources=evidence_sources,
        tool_calls=run.tool_calls,
        evidence=evidence_items,
        evidence_steps=evidence_steps,
        knowledge_refs=knowledge_refs,
    )

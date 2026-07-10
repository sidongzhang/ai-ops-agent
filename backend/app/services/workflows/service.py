"""Application service layer for approval workflows."""
import json
from datetime import datetime, timezone

from sqlmodel import Session

from app.agent.workflows.runner import resume_workflow, start_workflow
from app.models.auth import User
from app.models.workflows import ActionWorkflow
from app.repositories.systems import list_enabled_services_for_system
from app.repositories.workflows import get_workflow_for_org, list_workflows_for_system
from app.schemas import WorkflowDecision, WorkflowOut, WorkflowStart
from app.services.descriptors.builder import system_to_descriptor
from app.services.audit import record_audit_event
from app.services.systems.restart import annotate_restart_action, has_restart_permission
from app.services.systems.service import require_system


def to_workflow_out(workflow: ActionWorkflow) -> WorkflowOut:
    return WorkflowOut(
        id=workflow.id,
        thread_id=workflow.thread_id,
        status=workflow.status,
        question=workflow.question,
        diagnosis=workflow.diagnosis,
        proposed_action=workflow.proposed_action,
        requested_by_user_id=workflow.requested_by_user_id,
        approved_by_user_id=workflow.approved_by_user_id,
        target_service=workflow.target_service,
        target_resource=workflow.target_resource,
        execution_mode=workflow.execution_mode,
        execution_result=workflow.execution_result,
        approved_at=workflow.approved_at,
        executed_at=workflow.executed_at,
        created_at=workflow.created_at,
    )


def _apply_action_metadata(workflow: ActionWorkflow, descriptor: dict, proposed_action: dict) -> None:
    enriched = annotate_restart_action(descriptor, proposed_action)
    workflow.proposed_action = enriched
    workflow.target_service = str(enriched.get("service", "") or "")
    workflow.target_resource = str(enriched.get("target_resource", "") or "")
    workflow.execution_mode = str(enriched.get("execution_mode", "") or "")


def _workflow_question(question: str, context: dict) -> str:
    if context.get("analysis_type") != "stuck_tasks":
        return question
    raw_worker_health = context.get("worker_health", [])
    if not isinstance(raw_worker_health, list):
        raw_worker_health = []
    worker_health = [
        {
            "name": str(item.get("name", ""))[:100],
            "ok": bool(item.get("ok")),
            "detail": str(item.get("detail", ""))[:300],
        }
        for item in raw_worker_health[:20]
        if isinstance(item, dict) and item.get("name")
    ]
    safe_context = {
        "analysis_type": "stuck_tasks",
        "stuck_count": _bounded_int(context.get("stuck_count"), default=0, lower=0, upper=1000000000),
        "stuck_threshold_minutes": _bounded_int(
            context.get("stuck_threshold_minutes"),
            default=30,
            lower=1,
            upper=10080,
        ),
        "worker_health": worker_health,
    }
    return (
        f"{question}\n\n"
        "平台刚完成只读任务卡住分析，以下是已验证的结构化证据。"
        "请优先基于证据提出操作；不要修改业务任务数据。若 Worker 异常，可建议检查或重启对应服务，仍需审批。\n"
        f"{json.dumps(safe_context, ensure_ascii=False)}"
    )


def _bounded_int(value, *, default: int, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(parsed, upper))


async def create_workflow(
    session: Session,
    system_id: int,
    org_id: int,
    body: WorkflowStart,
    current_user: User,
) -> WorkflowOut:
    system = require_system(session, system_id, org_id)
    descriptor = system_to_descriptor(system, list_enabled_services_for_system(session, system_id))

    workflow = ActionWorkflow(
        org_id=org_id,
        system_id=system_id,
        requested_by_user_id=current_user.id,
        thread_id="",
        question=body.question,
    )
    session.add(workflow)
    session.commit()
    session.refresh(workflow)

    try:
        thread_id, diagnosis, proposed_action = await start_workflow(
            system_id=system_id,
            org_id=org_id,
            question=_workflow_question(body.question, body.context),
            descriptor=descriptor,
        )
    except Exception as exc:
        workflow.status = "error"
        workflow.execution_result = str(exc)
        workflow.updated_at = datetime.now(timezone.utc)
        session.add(workflow)
        session.commit()
        raise RuntimeError(f"AI 分析失败: {exc}") from exc

    workflow.thread_id = thread_id
    workflow.diagnosis = diagnosis
    _apply_action_metadata(workflow, descriptor, proposed_action)
    workflow.updated_at = datetime.now(timezone.utc)
    session.add(workflow)
    record_audit_event(
        session,
        org_id=org_id,
        system_id=system_id,
        actor_type="user",
        actor_id=str(current_user.id),
        event_type="workflow.created",
        target_type="workflow",
        target_id=str(workflow.id),
        status="success",
        input={
            "question": body.question,
            "analysis_type": body.context.get("analysis_type", ""),
            "stuck_count": body.context.get("stuck_count"),
        },
        output={"diagnosis": workflow.diagnosis, "proposed_action": workflow.proposed_action},
    )
    session.commit()
    session.refresh(workflow)
    return to_workflow_out(workflow)


def list_workflows(session: Session, system_id: int, org_id: int) -> list[WorkflowOut]:
    require_system(session, system_id, org_id)
    return [to_workflow_out(workflow) for workflow in list_workflows_for_system(session, system_id)]


def get_workflow(session: Session, system_id: int, workflow_id: int, org_id: int) -> WorkflowOut:
    workflow = get_workflow_for_org(session, workflow_id, system_id, org_id)
    if not workflow:
        raise LookupError("工作流不存在")
    return to_workflow_out(workflow)


async def decide_workflow(
    session: Session,
    system_id: int,
    workflow_id: int,
    org_id: int,
    body: WorkflowDecision,
    current_user: User,
) -> WorkflowOut:
    workflow = get_workflow_for_org(session, workflow_id, system_id, org_id)
    if not workflow:
        raise LookupError("工作流不存在")
    if workflow.status != "pending":
        raise ValueError(f"工作流已结束（status={workflow.status}），不可重复决策")

    system = require_system(session, system_id, org_id)
    if workflow.proposed_action.get("type") == "restart_container" and not has_restart_permission(current_user, system):
        raise PermissionError("当前用户没有该系统的重启权限")

    if not body.approved:
        workflow.status = "rejected"
        workflow.execution_result = body.reason or "用户拒绝"
        workflow.updated_at = datetime.now(timezone.utc)
        session.add(workflow)
        record_audit_event(
            session,
            org_id=org_id,
            system_id=system_id,
            actor_type="user",
            actor_id=str(current_user.id),
            event_type="workflow.rejected",
            target_type="workflow",
            target_id=str(workflow.id),
            status="success",
            input={"reason": body.reason},
            output={"question": workflow.question},
        )
        session.commit()
        session.refresh(workflow)
        return to_workflow_out(workflow)

    workflow.status = "approved"
    workflow.approved_by_user_id = current_user.id
    workflow.approved_at = datetime.now(timezone.utc)
    workflow.updated_at = workflow.approved_at
    session.add(workflow)
    record_audit_event(
        session,
        org_id=org_id,
        system_id=system_id,
        actor_type="user",
        actor_id=str(current_user.id),
        event_type="workflow.approved",
        target_type="workflow",
        target_id=str(workflow.id),
        status="success",
        input={"question": workflow.question},
        output={"proposed_action": workflow.proposed_action},
    )
    session.commit()

    try:
        await resume_workflow(workflow.thread_id)
    except Exception as exc:
        workflow.status = "error"
        workflow.execution_result = str(exc)
        workflow.updated_at = datetime.now(timezone.utc)
        workflow.executed_at = workflow.updated_at
        session.add(workflow)
        record_audit_event(
            session,
            org_id=org_id,
            system_id=system_id,
            actor_type="platform",
            actor_id="workflow",
            event_type="workflow.execution_failed",
            target_type="workflow",
            target_id=str(workflow.id),
            status="error",
            input={"thread_id": workflow.thread_id},
            output={"error": str(exc)},
        )
        session.commit()
        raise RuntimeError(f"执行失败: {exc}") from exc

    session.refresh(workflow)
    record_audit_event(
        session,
        org_id=org_id,
        system_id=system_id,
        actor_type="platform",
        actor_id="workflow",
        event_type="workflow.executed",
        target_type="workflow",
        target_id=str(workflow.id),
        status=workflow.status,
        input={"thread_id": workflow.thread_id, "proposed_action": workflow.proposed_action},
        output={"execution_result": workflow.execution_result},
        commit=True,
    )
    session.refresh(workflow)
    return to_workflow_out(workflow)

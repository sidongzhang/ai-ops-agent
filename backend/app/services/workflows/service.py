"""Application service layer for approval workflows."""
from datetime import datetime, timezone

from sqlmodel import Session

from app.agent.workflows.runner import resume_workflow, start_workflow
from app.models.workflows import ActionWorkflow
from app.repositories.systems import list_services_for_system
from app.repositories.workflows import get_workflow_for_org, list_workflows_for_system
from app.schemas import WorkflowDecision, WorkflowOut, WorkflowStart
from app.services.descriptors.builder import system_to_descriptor
from app.services.systems.service import require_system


def to_workflow_out(workflow: ActionWorkflow) -> WorkflowOut:
    return WorkflowOut(
        id=workflow.id,
        thread_id=workflow.thread_id,
        status=workflow.status,
        question=workflow.question,
        diagnosis=workflow.diagnosis,
        proposed_action=workflow.proposed_action,
        execution_result=workflow.execution_result,
        created_at=workflow.created_at,
    )


async def create_workflow(session: Session, system_id: int, org_id: int, body: WorkflowStart) -> WorkflowOut:
    system = require_system(session, system_id, org_id)
    descriptor = system_to_descriptor(system, list_services_for_system(session, system_id))

    workflow = ActionWorkflow(
        org_id=org_id,
        system_id=system_id,
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
            question=body.question,
            descriptor=descriptor,
        )
    except Exception as exc:
        workflow.status = "error"
        workflow.execution_result = str(exc)
        workflow.updated_at = datetime.now(timezone.utc)
        session.add(workflow)
        session.commit()
        raise RuntimeError(f"AI 分析失败: {exc}")

    workflow.thread_id = thread_id
    workflow.diagnosis = diagnosis
    workflow.proposed_action = proposed_action
    workflow.updated_at = datetime.now(timezone.utc)
    session.add(workflow)
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
) -> WorkflowOut:
    workflow = get_workflow_for_org(session, workflow_id, system_id, org_id)
    if not workflow:
        raise LookupError("工作流不存在")
    if workflow.status != "pending":
        raise ValueError(f"工作流已结束（status={workflow.status}），不可重复决策")

    if not body.approved:
        workflow.status = "rejected"
        workflow.execution_result = body.reason or "用户拒绝"
        workflow.updated_at = datetime.now(timezone.utc)
        session.add(workflow)
        session.commit()
        session.refresh(workflow)
        return to_workflow_out(workflow)

    workflow.status = "approved"
    workflow.updated_at = datetime.now(timezone.utc)
    session.add(workflow)
    session.commit()

    try:
        await resume_workflow(workflow.thread_id)
    except Exception as exc:
        workflow.status = "error"
        workflow.execution_result = str(exc)
        workflow.updated_at = datetime.now(timezone.utc)
        session.add(workflow)
        session.commit()
        raise RuntimeError(f"执行失败: {exc}")

    session.refresh(workflow)
    return to_workflow_out(workflow)

"""
LangGraph 审批闸接口。

POST /systems/{id}/workflow          → 启动工作流（AI 分析 + 提案，人工审批前暂停）
GET  /systems/{id}/workflow          → 列出该系统的工作流
GET  /systems/{id}/workflow/{wf_id}  → 单条工作流详情
POST /systems/{id}/workflow/{wf_id}/decision → 审批决策（通过/拒绝）
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..agent.approval_graph import resume_workflow, start_workflow
from ..db import get_session
from ..deps import get_current_org_id
from ..descriptors import system_to_descriptor
from ..models import ActionWorkflow, Collector, MonitoredSystem, Service
from ..schemas import WorkflowDecision, WorkflowOut, WorkflowStart

router = APIRouter(prefix="/systems", tags=["workflow"])
log = logging.getLogger(__name__)


def _services_of(session: Session, system_id: int) -> list:
    return list(session.exec(select(Service).where(Service.system_id == system_id)))


def _to_out(wf: ActionWorkflow) -> WorkflowOut:
    return WorkflowOut(
        id=wf.id,
        thread_id=wf.thread_id,
        status=wf.status,
        question=wf.question,
        diagnosis=wf.diagnosis,
        proposed_action=wf.proposed_action,
        execution_result=wf.execution_result,
        created_at=wf.created_at,
    )


@router.post("/{system_id}/workflow", response_model=WorkflowOut, status_code=202)
async def create_workflow(
    system_id: int,
    body: WorkflowStart,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    """启动审批工作流：AI 分析问题，提案修复动作，等待人工审批。"""
    system = session.exec(
        select(MonitoredSystem).where(
            MonitoredSystem.id == system_id,
            MonitoredSystem.org_id == org_id,
        )
    ).first()
    if not system:
        raise HTTPException(404, "系统不存在")

    services = _services_of(session, system_id)
    descriptor = system_to_descriptor(system, services)

    # 先在 DB 占位（status=pending，LangGraph 运行过程中可能耗时数秒）
    wf = ActionWorkflow(
        org_id=org_id,
        system_id=system_id,
        thread_id="",       # 运行后填入
        question=body.question,
    )
    session.add(wf)
    session.commit()
    session.refresh(wf)

    try:
        thread_id, diagnosis, proposed_action = await start_workflow(
            system_id=system_id,
            org_id=org_id,
            question=body.question,
            descriptor=descriptor,
        )
    except Exception as e:
        wf.status = "error"
        wf.execution_result = str(e)
        wf.updated_at = datetime.now(timezone.utc)
        session.add(wf)
        session.commit()
        raise HTTPException(500, f"AI 分析失败: {e}")

    wf.thread_id = thread_id
    wf.diagnosis = diagnosis
    wf.proposed_action = proposed_action
    wf.updated_at = datetime.now(timezone.utc)
    session.add(wf)
    session.commit()
    session.refresh(wf)
    return _to_out(wf)


@router.get("/{system_id}/workflow", response_model=list[WorkflowOut])
def list_workflows(
    system_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    system = session.exec(
        select(MonitoredSystem).where(
            MonitoredSystem.id == system_id, MonitoredSystem.org_id == org_id
        )
    ).first()
    if not system:
        raise HTTPException(404, "系统不存在")
    wfs = session.exec(
        select(ActionWorkflow)
        .where(ActionWorkflow.system_id == system_id)
        .order_by(ActionWorkflow.created_at.desc())
        .limit(50)
    ).all()
    return [_to_out(w) for w in wfs]


@router.get("/{system_id}/workflow/{wf_id}", response_model=WorkflowOut)
def get_workflow(
    system_id: int,
    wf_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    wf = session.exec(
        select(ActionWorkflow).where(
            ActionWorkflow.id == wf_id,
            ActionWorkflow.system_id == system_id,
            ActionWorkflow.org_id == org_id,
        )
    ).first()
    if not wf:
        raise HTTPException(404, "工作流不存在")
    return _to_out(wf)


@router.post("/{system_id}/workflow/{wf_id}/decision", response_model=WorkflowOut)
async def decide_workflow(
    system_id: int,
    wf_id: int,
    body: WorkflowDecision,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    """提交审批决策。通过（approved=true）时自动执行提案动作并返回结果。"""
    wf = session.exec(
        select(ActionWorkflow).where(
            ActionWorkflow.id == wf_id,
            ActionWorkflow.system_id == system_id,
            ActionWorkflow.org_id == org_id,
        )
    ).first()
    if not wf:
        raise HTTPException(404, "工作流不存在")
    if wf.status != "pending":
        raise HTTPException(400, f"工作流已结束（status={wf.status}），不可重复决策")

    if not body.approved:
        wf.status = "rejected"
        wf.execution_result = body.reason or "用户拒绝"
        wf.updated_at = datetime.now(timezone.utc)
        session.add(wf)
        session.commit()
        session.refresh(wf)
        return _to_out(wf)

    # 审批通过：恢复 LangGraph 执行
    wf.status = "approved"
    wf.updated_at = datetime.now(timezone.utc)
    session.add(wf)
    session.commit()

    try:
        final_status, execution_result = await resume_workflow(wf.thread_id)
    except Exception as e:
        wf.status = "error"
        wf.execution_result = str(e)
        wf.updated_at = datetime.now(timezone.utc)
        session.add(wf)
        session.commit()
        raise HTTPException(500, f"执行失败: {e}")

    # execute_node 已直接更新 DB，这里 refresh 拿最新值
    session.refresh(wf)
    return _to_out(wf)

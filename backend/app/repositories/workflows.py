"""Persistence helpers for approval workflows."""
from sqlmodel import Session, select

from ..models.workflows import ActionWorkflow


def get_workflow_for_org(
    session: Session,
    workflow_id: int,
    system_id: int,
    org_id: int,
) -> ActionWorkflow | None:
    return session.exec(
        select(ActionWorkflow).where(
            ActionWorkflow.id == workflow_id,
            ActionWorkflow.system_id == system_id,
            ActionWorkflow.org_id == org_id,
        )
    ).first()


def list_workflows_for_system(session: Session, system_id: int) -> list[ActionWorkflow]:
    return list(
        session.exec(
            select(ActionWorkflow)
            .where(ActionWorkflow.system_id == system_id)
            .order_by(ActionWorkflow.created_at.desc())
            .limit(50)
        )
    )

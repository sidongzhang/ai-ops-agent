"""Persistence helpers for approval workflows."""
from sqlmodel import Session, func, select

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


def list_workflows_for_org(
    session: Session,
    org_id: int,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ActionWorkflow]:
    limit = max(1, min(limit, 100))
    query = _workflows_query(session, org_id, status=status)
    query = query.order_by(ActionWorkflow.created_at.desc()).offset(max(offset, 0)).limit(limit)
    return list(session.exec(query).all())


def count_workflows_for_org(
    session: Session,
    org_id: int,
    *,
    status: str | None = None,
) -> int:
    query = _workflows_query(session, org_id, status=status)
    return session.exec(select(func.count()).select_from(query.subquery())).one()


def _workflows_query(session: Session, org_id: int, *, status: str | None = None):
    query = select(ActionWorkflow).where(ActionWorkflow.org_id == org_id)
    if status == "pending":
        query = query.where(ActionWorkflow.status == "pending")
    elif status == "processed":
        query = query.where(ActionWorkflow.status != "pending")
    elif status in {"approved", "done", "rejected", "error"}:
        query = query.where(ActionWorkflow.status == status)
    return query

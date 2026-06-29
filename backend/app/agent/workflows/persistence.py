"""Persistence helpers for workflow runtime."""
from datetime import datetime, timezone

from sqlmodel import Session, select

from ...core.database import engine
from ...models.workflows import ActionWorkflow


def update_workflow_db(thread_id: str, status: str, result: str) -> None:
    with Session(engine) as session:
        workflow = session.exec(
            select(ActionWorkflow).where(ActionWorkflow.thread_id == thread_id)
        ).first()
        if workflow:
            workflow.status = status
            workflow.execution_result = result
            workflow.updated_at = datetime.now(timezone.utc)
            session.add(workflow)
            session.commit()

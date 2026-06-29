"""LangGraph workflow state."""
from typing_extensions import TypedDict


class WorkflowState(TypedDict):
    system_id: int
    org_id: int
    thread_id: str
    question: str
    descriptor: dict
    diagnosis: str
    proposed_action: dict
    execution_result: str
    final_status: str

"""Graph assembly and public workflow entrypoints."""
import uuid

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .nodes import analyze_node, execute_node
from .state import WorkflowState

checkpointer = MemorySaver()

builder = StateGraph(WorkflowState)
builder.add_node("analyze", analyze_node)
builder.add_node("execute", execute_node)
builder.add_edge(START, "analyze")
builder.add_edge("analyze", "execute")
builder.add_edge("execute", END)

approval_graph = builder.compile(
    checkpointer=checkpointer,
    interrupt_before=["execute"],
)


async def start_workflow(
    system_id: int,
    org_id: int,
    question: str,
    descriptor: dict,
) -> tuple[str, str, dict]:
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    state_input: WorkflowState = {
        "system_id": system_id,
        "org_id": org_id,
        "thread_id": thread_id,
        "question": question,
        "descriptor": descriptor,
        "diagnosis": "",
        "proposed_action": {},
        "execution_result": "",
        "final_status": "",
    }
    await approval_graph.ainvoke(state_input, config)
    snapshot = await approval_graph.aget_state(config)
    return (
        thread_id,
        snapshot.values.get("diagnosis", ""),
        snapshot.values.get("proposed_action", {}),
    )


async def resume_workflow(thread_id: str) -> tuple[str, str]:
    config = {"configurable": {"thread_id": thread_id}}
    await approval_graph.ainvoke(None, config)
    snapshot = await approval_graph.aget_state(config)
    return (
        snapshot.values.get("final_status", "done"),
        snapshot.values.get("execution_result", ""),
    )

"""Workflow graph nodes."""
import logging

from .models import AnalysisDeps, AnalysisResult, analysis_agent
from .persistence import update_workflow_db
from .state import WorkflowState
from ...services.systems.restart import annotate_restart_action
from ...services.workflows.execution import execute_workflow_action, verify_action_recovery

log = logging.getLogger(__name__)


async def analyze_node(state: WorkflowState) -> dict:
    try:
        result = await analysis_agent.run(
            state["question"],
            deps=AnalysisDeps(descriptor=state["descriptor"]),
        )
        analysis: AnalysisResult = result.output
        proposed_action = annotate_restart_action(
            state["descriptor"],
            analysis.proposed_action.model_dump(),
        )
        log.info("[workflow:%s] 分析完成，提案类型=%s", state["thread_id"], proposed_action.get("type"))
        return {
            "diagnosis": analysis.diagnosis,
            "proposed_action": proposed_action,
        }
    except Exception as exc:
        log.error("[workflow:%s] 分析失败: %s", state["thread_id"], exc)
        return {
            "diagnosis": f"分析失败: {exc}",
            "proposed_action": {"type": "manual", "description": "请人工排查", "manual_steps": []},
        }


async def execute_node(state: WorkflowState) -> dict:
    thread_id = state["thread_id"]
    action = state.get("proposed_action", {})

    try:
        execution_result = await execute_workflow_action(state, action)
        recovered, verification = verify_action_recovery(state, action)
        combined_result = f"{execution_result}\n\n恢复回查：{verification}"
        final_status = "done" if recovered else "error"
        update_workflow_db(thread_id, final_status, combined_result, executed=True)
        log.info("[workflow:%s] 执行完成，回查=%s", thread_id, recovered)
        return {"execution_result": combined_result, "final_status": final_status}
    except Exception as exc:
        error = str(exc)
        update_workflow_db(thread_id, "error", error, executed=True)
        log.error("[workflow:%s] 执行失败: %s", thread_id, error)
        return {"execution_result": error, "final_status": "error"}

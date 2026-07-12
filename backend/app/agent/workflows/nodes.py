"""Workflow graph nodes."""
import logging

from .models import AnalysisDeps, AnalysisResult, analysis_agent
from .persistence import update_workflow_db
from .state import WorkflowState
from ...services.systems.restart import annotate_restart_action
from ...services.workflows.actions import normalize_action
from ...services.workflows.execution import execute_workflow_action, verify_action_recovery_async

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
        proposed_action = normalize_action(proposed_action, descriptor=state["descriptor"])
        log.info("[workflow:%s] 分析完成，提案类型=%s", state["thread_id"], proposed_action.get("type"))
        return {
            "diagnosis": analysis.diagnosis,
            "proposed_action": proposed_action,
        }
    except Exception as exc:
        log.error("[workflow:%s] 分析失败: %s", state["thread_id"], exc)
        err_str = str(exc)
        if "Exceeded maximum output retries" in err_str or "retries" in err_str.lower():
            err_type, guidance = "模型输出异常", (
                "AI 模型生成诊断时反复出错。这可能是因为问题描述不够清晰，"
                "或系统描述符信息不足。建议补充更多上下文后重试，"
                "或直接进入系统详情页手动查看监控数据。"
            )
        elif "401" in err_str or "Unauthorized" in err_str or "API key" in err_str.lower():
            err_type, guidance = "AI 服务认证失败", (
                "AI 模型 API 密钥无效或已过期。"
                "请检查后端 .env 文件中的 DEEPSEEK_API_KEY 是否正确配置。"
            )
        elif "timeout" in err_str.lower() or "timed out" in err_str.lower():
            err_type, guidance = "AI 服务超时", (
                "AI 模型服务响应超时，可能是网络问题或模型负载过高，请稍后重试。"
            )
        else:
            err_type, guidance = "诊断异常", f"AI 诊断遇到错误: {err_str[:200]}"
        return {
            "diagnosis": f"分析失败（{err_type}）",
            "proposed_action": {
                "type": "manual",
                "description": guidance,
                "manual_steps": [
                    "确认 AI 模型服务是否正常运行（检查 API KEY 和网络）",
                    "检查系统服务配置（健康检查地址、容器名称等）",
                    "如问题持续，可进入系统详情页手动查看监控数据和日志",
                ],
            },
        }


async def execute_node(state: WorkflowState) -> dict:
    thread_id = state["thread_id"]
    action = state.get("proposed_action", {})

    try:
        execution_result = await execute_workflow_action(state, action)
        recovered, verification = await verify_action_recovery_async(state, action)
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

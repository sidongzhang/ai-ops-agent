"""Workflow graph nodes."""
import logging

from sqlmodel import Session, select

from ...core.database import engine
from ...models.collectors import Collector
from ...services.realtime.websocket import manager
from .models import AnalysisDeps, AnalysisResult, analysis_agent
from .persistence import update_workflow_db
from .state import WorkflowState

log = logging.getLogger(__name__)


async def analyze_node(state: WorkflowState) -> dict:
    try:
        result = await analysis_agent.run(
            state["question"],
            deps=AnalysisDeps(descriptor=state["descriptor"]),
        )
        analysis: AnalysisResult = result.output
        log.info(f"[workflow:{state['thread_id']}] 分析完成，提案类型={analysis.proposed_action.type}")
        return {
            "diagnosis": analysis.diagnosis,
            "proposed_action": analysis.proposed_action.model_dump(),
        }
    except Exception as exc:
        log.error(f"[workflow:{state['thread_id']}] 分析失败: {exc}")
        return {
            "diagnosis": f"分析失败: {exc}",
            "proposed_action": {"type": "manual", "description": "请人工排查", "manual_steps": []},
        }


async def execute_node(state: WorkflowState) -> dict:
    thread_id = state["thread_id"]
    action = state.get("proposed_action", {})
    action_type = action.get("type", "manual")

    try:
        if action_type in ("fetch_logs", "health_check"):
            with Session(engine) as session:
                collector = session.exec(
                    select(Collector).where(Collector.system_id == state["system_id"])
                ).first()

            if not collector:
                raise RuntimeError("该系统没有采集器，无法自动执行")
            if not manager.is_connected(collector.id):
                raise RuntimeError("采集器当前离线，请先确认采集器在线后重试")

            command_args = {"service": action.get("service", "")}
            command_args.update(action.get("args", {}))
            command_result = await manager.send_command(collector.id, action_type, command_args)

            if not command_result.get("ok"):
                raise RuntimeError(command_result.get("result", "采集器返回错误"))
            raw = command_result["result"]
            if isinstance(raw, list):
                execution_result = "\n".join(
                    f"  {item['name']}: {item['detail']} {'✅' if item['ok'] else '❌'}" for item in raw
                )
            else:
                execution_result = str(raw)
        else:
            steps = action.get("manual_steps", [])
            if steps:
                execution_result = "已记录，请按以下步骤手动执行：\n" + "\n".join(
                    f"  {index + 1}. {step}" for index, step in enumerate(steps)
                )
            else:
                execution_result = "已批准，请参照诊断结果手动处理。"

        update_workflow_db(thread_id, "done", execution_result)
        log.info(f"[workflow:{thread_id}] 执行完成")
        return {"execution_result": execution_result, "final_status": "done"}
    except Exception as exc:
        error = str(exc)
        update_workflow_db(thread_id, "error", error)
        log.error(f"[workflow:{thread_id}] 执行失败: {error}")
        return {"execution_result": error, "final_status": "error"}

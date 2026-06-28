"""
LangGraph 审批闸 —— AI 提案 → 人工审批 → 自动执行。

图结构：
  START → analyze_node ──[interrupt_before]──> execute_node → END

analyze_node:
  使用 Pydantic AI 结构化输出（AnalysisResult），给出诊断文字 + 提案动作。
  提案动作类型：
    fetch_logs   → 通过采集器 WS 下行拉取服务日志（需采集器在线）
    health_check → 通过采集器 WS 下行检查服务健康（需采集器在线）
    manual       → 高危/不可逆操作，返回供人工执行的分步骤说明

execute_node:
  审批通过后由平台自动调用（resume）。
  fetch_logs/health_check 经 ws.manager 向采集器下发命令，同步等待结果。
  manual 类型直接格式化步骤文本返回，不做自动执行。
  执行结果写回 state 并更新 DB。

MemorySaver 作为 dev 检查点（进程重启后 pending 工作流不可恢复）。
生产替换为 langgraph.checkpoint.postgres.aio.AsyncPostgresSaver。
"""
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from ..config import settings

log = logging.getLogger(__name__)


# ── Pydantic AI 结构化输出模型 ─────────────────────────────────


class ProposedAction(BaseModel):
    type: Literal["fetch_logs", "health_check", "manual"]
    description: str
    service: str = ""      # 目标服务名（fetch_logs/health_check 必填）
    args: dict = {}        # 额外参数（如 lines、keyword）
    manual_steps: list[str] = []   # manual 类型的分步骤说明


class AnalysisResult(BaseModel):
    diagnosis: str
    proposed_action: ProposedAction


# ── Pydantic AI Agent（结构化输出版）─────────────────────────


@dataclass
class AnalysisDeps:
    descriptor: dict


def _make_analysis_model() -> OpenAIChatModel:
    return OpenAIChatModel(
        settings.agent_model,
        provider=OpenAIProvider(
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key,
        ),
    )


analysis_agent = Agent(
    _make_analysis_model(),
    output_type=AnalysisResult,
    deps_type=AnalysisDeps,
    system_prompt="""你是智能运维专家。根据用户的问题和系统描述，提供：
1. 简明的诊断分析（diagnosis）
2. 一个建议执行的修复/排查动作（proposed_action）

动作类型规则：
- fetch_logs  ：当需要拉取服务日志来排查时使用；service 填服务名，args 可加 {"lines": 100}
- health_check：当需要验证某个服务是否恢复正常时使用；service 填服务名
- manual      ：需要人工执行的高危操作（重启、删数据、修配置）；manual_steps 列出具体步骤

优先选择风险低、可逆的动作。若问题已清晰且只需验证，选 health_check；
若需要看日志细节，选 fetch_logs；若需要危险操作，选 manual。""",
)


@analysis_agent.system_prompt
def _inject_descriptor(ctx: RunContext[AnalysisDeps]) -> str:
    return f"\n\n系统描述（JSON）：\n{json.dumps(ctx.deps.descriptor, ensure_ascii=False, indent=2)}"


# ── LangGraph 状态 ────────────────────────────────────────────


class WorkflowState(TypedDict):
    # 输入（启动时设置）
    system_id: int
    org_id: int
    thread_id: str
    question: str
    descriptor: dict
    # analyze_node 填写
    diagnosis: str
    proposed_action: dict
    # execute_node 填写
    execution_result: str
    final_status: str   # done / error


# ── Graph 节点 ───────────────────────────────────────────────


async def analyze_node(state: WorkflowState) -> dict:
    """调用 Pydantic AI 分析问题，产生诊断 + 提案动作。"""
    try:
        result = await analysis_agent.run(
            state["question"],
            deps=AnalysisDeps(descriptor=state["descriptor"]),
        )
        ar: AnalysisResult = result.output
        log.info(f"[workflow:{state['thread_id']}] 分析完成，提案类型={ar.proposed_action.type}")
        return {
            "diagnosis": ar.diagnosis,
            "proposed_action": ar.proposed_action.model_dump(),
        }
    except Exception as e:
        log.error(f"[workflow:{state['thread_id']}] 分析失败: {e}")
        return {
            "diagnosis": f"分析失败: {e}",
            "proposed_action": {"type": "manual", "description": "请人工排查", "manual_steps": []},
        }


async def execute_node(state: WorkflowState) -> dict:
    """执行已批准的提案动作，结果写回 state + DB。"""
    from ..db import engine
    from ..models import ActionWorkflow, Collector
    from ..ws.manager import manager
    from sqlmodel import Session, select

    thread_id = state["thread_id"]
    action = state.get("proposed_action", {})
    action_type = action.get("type", "manual")

    try:
        if action_type in ("fetch_logs", "health_check"):
            # 找采集器
            with Session(engine) as session:
                collector = session.exec(
                    select(Collector).where(Collector.system_id == state["system_id"])
                ).first()

            if not collector:
                raise RuntimeError("该系统没有采集器，无法自动执行")
            if not manager.is_connected(collector.id):
                raise RuntimeError("采集器当前离线，请先确认采集器在线后重试")

            cmd_args = {"service": action.get("service", "")}
            cmd_args.update(action.get("args", {}))
            cmd_result = await manager.send_command(collector.id, action_type, cmd_args)

            if not cmd_result.get("ok"):
                raise RuntimeError(cmd_result.get("result", "采集器返回错误"))
            raw = cmd_result["result"]
            # health_check 返回 list，转可读文字
            if isinstance(raw, list):
                execution_result = "\n".join(
                    f"  {r['name']}: {r['detail']} {'✅' if r['ok'] else '❌'}" for r in raw
                )
            else:
                execution_result = str(raw)

        else:  # manual
            steps = action.get("manual_steps", [])
            if steps:
                execution_result = "已记录，请按以下步骤手动执行：\n" + "\n".join(
                    f"  {i+1}. {s}" for i, s in enumerate(steps)
                )
            else:
                execution_result = "已批准，请参照诊断结果手动处理。"

        # 更新 DB
        _update_workflow_db(thread_id, "done", execution_result)
        log.info(f"[workflow:{thread_id}] 执行完成")
        return {"execution_result": execution_result, "final_status": "done"}

    except Exception as e:
        err = str(e)
        _update_workflow_db(thread_id, "error", err)
        log.error(f"[workflow:{thread_id}] 执行失败: {err}")
        return {"execution_result": err, "final_status": "error"}


def _update_workflow_db(thread_id: str, status: str, result: str) -> None:
    from ..db import engine
    from ..models import ActionWorkflow
    from sqlmodel import Session, select

    with Session(engine) as session:
        wf = session.exec(
            select(ActionWorkflow).where(ActionWorkflow.thread_id == thread_id)
        ).first()
        if wf:
            wf.status = status
            wf.execution_result = result
            wf.updated_at = datetime.now(timezone.utc)
            session.add(wf)
            session.commit()


# ── Graph 编译（单例）────────────────────────────────────────

_checkpointer = MemorySaver()

_builder = StateGraph(WorkflowState)
_builder.add_node("analyze", analyze_node)
_builder.add_node("execute", execute_node)
_builder.add_edge(START, "analyze")
_builder.add_edge("analyze", "execute")
_builder.add_edge("execute", END)

approval_graph = _builder.compile(
    checkpointer=_checkpointer,
    interrupt_before=["execute"],   # 分析完暂停，等待人工审批
)


# ── 公开 API ─────────────────────────────────────────────────


async def start_workflow(
    system_id: int,
    org_id: int,
    question: str,
    descriptor: dict,
) -> tuple[str, str, dict]:
    """
    启动工作流：运行 analyze_node，在 execute_node 前暂停。
    返回 (thread_id, diagnosis, proposed_action)。
    """
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
    # 图在 execute_node 前暂停，从 checkpoint 读取 analyze_node 的输出
    snap = await approval_graph.aget_state(config)
    diagnosis = snap.values.get("diagnosis", "")
    proposed_action = snap.values.get("proposed_action", {})
    return thread_id, diagnosis, proposed_action


async def resume_workflow(thread_id: str) -> tuple[str, str]:
    """
    审批通过后恢复工作流，执行 execute_node。
    返回 (final_status, execution_result)。
    """
    config = {"configurable": {"thread_id": thread_id}}
    await approval_graph.ainvoke(None, config)
    snap = await approval_graph.aget_state(config)
    return (
        snap.values.get("final_status", "done"),
        snap.values.get("execution_result", ""),
    )

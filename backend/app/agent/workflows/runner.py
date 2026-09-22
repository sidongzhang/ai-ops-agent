"""Graph assembly and public workflow entrypoints.

Checkpointer 策略：
  • 导入期默认用 MemorySaver 编译（测试 / 无 PG 环境零依赖可用）。
  • 应用启动（lifespan）时若 DATABASE_URL 为 Postgres，则切换为官方
    AsyncPostgresSaver —— 审批挂起的工作流跨重启存活；失败自动降级内存版。
"""
import logging
import uuid
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.core.config import settings

from .nodes import analyze_node, execute_node
from .state import WorkflowState

log = logging.getLogger(__name__)


def _build_graph(checkpointer: BaseCheckpointSaver):
    builder = StateGraph(WorkflowState)
    builder.add_node("analyze", analyze_node)
    builder.add_node("execute", execute_node)
    builder.add_edge(START, "analyze")
    builder.add_edge("analyze", "execute")
    builder.add_edge("execute", END)
    return builder.compile(checkpointer=checkpointer, interrupt_before=["execute"])


_checkpointer: BaseCheckpointSaver | None = None
_pool = None                    # psycopg AsyncConnectionPool，shutdown 时关闭
approval_graph = _build_graph(MemorySaver())


def _get_graph():
    return approval_graph


def _pg_conninfo() -> str:
    """把 settings.database_url（可能带 +psycopg2 后缀）转成 psycopg3 连接串。"""
    from sqlalchemy.engine import make_url

    url = make_url(str(settings.database_url))
    url = url.set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


async def init_persistent_checkpointer() -> bool:
    """Postgres 持久化 checkpointer；不适用或失败时保持内存版并返回 False。"""
    global approval_graph, _checkpointer, _pool
    if not str(settings.database_url).startswith("postgresql"):
        log.info("[workflow] DATABASE_URL 非 Postgres，审批 checkpointer 使用内存版")
        return False
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(
            conninfo=_pg_conninfo(),
            min_size=1,
            max_size=5,
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0},
        )
        await pool.open()
        saver = AsyncPostgresSaver(pool)
        await saver.setup()   # 幂等建表：checkpoints / checkpoint_blobs / checkpoint_writes
        _checkpointer = saver
        _pool = pool
        approval_graph = _build_graph(saver)
        log.info("[workflow] 审批工作流 checkpointer: AsyncPostgresSaver（持久化）")
        return True
    except Exception as exc:  # pragma: no cover - 启动期防御
        log.warning(f"[workflow] Postgres checkpointer 初始化失败，降级内存版: {exc}")
        return False


async def shutdown_checkpointer() -> None:
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        except Exception:
            pass
        _pool = None


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
    graph = _get_graph()
    await graph.ainvoke(state_input, config)
    snapshot = await graph.aget_state(config)
    return (
        thread_id,
        snapshot.values.get("diagnosis", ""),
        snapshot.values.get("proposed_action", {}),
    )


async def resume_workflow(thread_id: str) -> tuple[str, str]:
    config = {"configurable": {"thread_id": thread_id}}
    graph = _get_graph()
    await graph.ainvoke(None, config)
    snapshot = await graph.aget_state(config)
    return (
        snapshot.values.get("final_status", "done"),
        snapshot.values.get("execution_result", ""),
    )

"""AIOps 运维工具 MCP Server——把平台的只读运维能力暴露为 MCP 协议服务。

价值：
  * 任何 MCP 客户端（Claude Desktop / Cursor / 其他 Agent）都能直接使用这套运维工具
  * 新数据源接入从「改 Agent 代码」变为「注册一个 MCP 工具」
  * 平台自身的诊断 Agent 后续可切换为 MCP Client 动态发现工具（当前仍用原生工具集，
    避免 IPC 开销）

工具集（全部只读，与诊断 Agent 的取证白名单一致）：
  list_services / health_check / check_container_state / read_logs / search_logs /
  query_prometheus / run_redis_command / run_kafka_command

绑定目标系统：环境变量 MCP_SYSTEM_ID 指定 system_id（启动时从 Postgres 加载 descriptor）。

启动：
  stdio:  MCP_SYSTEM_ID=1 backend/.venv/bin/python -m app.agent.diagnostics.mcp_server
  sse:    MCP_SYSTEM_ID=1 backend/.venv/bin/python app/agent/diagnostics/mcp_server.py --transport sse --port 8765
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
# Direct execution must not resolve the adjacent mcp.py as the third-party SDK.
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != _here]
_backend = _here.parents[2]
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from mcp.server.mcpserver import MCPServer

from app.services.descriptors.builder import system_to_descriptor
from app.agent.harness.catalog import EVIDENCE_TOOLS
from app.agent.harness.execution import ExecutionTracker
from app.agent.harness.gateway import RunScope, ToolGateway

logging.basicConfig(level=logging.INFO, format="[mcp-server] %(levelname)s %(message)s")
log = logging.getLogger("aiops-mcp")

server = MCPServer(
    name="aiops-ops-tools",
    instructions=(
        "智能运维平台的只读运维工具集：服务探活、容器状态、日志检索、PromQL 查询、"
        "Redis/Kafka 只读命令。所有工具只读，写操作与危险子命令一律拒绝。"
    ),
    version="1.0.0",
)

_state: dict = {"descriptor": None}


def _descriptor() -> dict:
    d = _state.get("descriptor")
    if not d:
        raise RuntimeError(
            f"未配置目标系统：请设置环境变量 MCP_SYSTEM_ID（当前值={os.environ.get('MCP_SYSTEM_ID')!r}）"
        )
    return d


async def _load_descriptor() -> None:
    system_id = int(os.environ.get("MCP_SYSTEM_ID", "0"))
    if not system_id:
        log.warning("未设置 MCP_SYSTEM_ID，工具调用将返回配置错误")
        return
    from sqlmodel import Session, select

    from app.core.database import engine
    from app.models.systems import MonitoredSystem, Service

    with Session(engine) as session:
        system = session.get(MonitoredSystem, system_id)
        if not system:
            raise RuntimeError(f"system_id={system_id} 不存在")
        services = session.exec(
            select(Service).where(Service.system_id == system_id, Service.enabled == True)  # noqa: E712
        ).all()
        _state["descriptor"] = system_to_descriptor(system, services)
        _state["system_name"] = system.name
        _state['org_id'] = system.org_id
        _state['system_id'] = system.id
    log.info("已绑定系统 #%s %s", system_id, system.name)


async def _invoke(name, arguments):
    """MCP retains its public aliases; execution and authority use the shared gateway."""
    from types import SimpleNamespace
    from uuid import uuid4
    from app.agent.diagnostics.tools import AgentDeps
    from app.core.config import settings
    from app.services.diagnostics.service import _tool_audit_recorder

    descriptor = _descriptor()
    if not _state.get('org_id') or not _state.get('system_id'):
        raise RuntimeError('MCP系统身份尚未绑定，拒绝执行工具')
    scope = RunScope(_state['org_id'], _state['system_id'], 'mcp_' + uuid4().hex, 'mcp')
    tracker = ExecutionTracker()
    sink = _state.get('audit_sink') or _tool_audit_recorder(scope.org_id, scope.system_id, 'mcp', None)
    deps = AgentDeps(descriptor, scope=scope, allowed_tools=EVIDENCE_TOOLS,
                     tool_audit_sink=sink, execution_tracker=tracker)
    ctx = SimpleNamespace(deps=deps, tool_call_id=uuid4().hex)
    try:
        return (await ToolGateway().invoke(name, arguments, ctx, expected_scope=scope)).text
    finally:
        if not tracker.stop_confirmed():
            confirmed = await asyncio.to_thread(tracker.wait_for_stop, settings.diagnosis_stop_wait_seconds)
            if not confirmed:
                log.warning('MCP工具执行停止未确认 run=%s tool=%s', scope.run_id, name)


@server.tool(
    name="list_services",
    description="列出目标系统全部服务的健康状态（探活首选入手）",
)
async def list_services() -> str:
    return await _invoke('list_services', {})


@server.tool(
    name="health_check",
    description="检查单个服务的健康状态（探活 + 详情）",
)
async def health_check(service: str) -> str:
    return await _invoke('check_service', {'service': service})


@server.tool(
    name="check_container_state",
    description="查容器真实状态：运行/退出原因/OOMKilled/退出码/内存上限/重启次数",
)
async def check_container_state(service: str) -> str:
    return await _invoke('check_container_state', {'service': service})


@server.tool(
    name="read_logs",
    description="读取某服务最近 N 行日志",
)
async def read_logs(service: str, lines: int = 50) -> str:
    return await _invoke('read_logs', {'service': service, 'lines': max(1, min(lines, 300))})


@server.tool(
    name="search_logs",
    description="在某服务日志中按关键词搜索（最近 500 行内）",
)
async def search_logs(service: str, keyword: str, lines: int = 200) -> str:
    return await _invoke('search_logs', {'service': service, 'keyword': keyword, 'lines': max(1, min(lines, 500))})


@server.tool(
    name="query_prometheus",
    description="执行 PromQL 即时查询（最多返回 8 条序列）",
)
async def query_prometheus(promql: str) -> str:
    return await _invoke('query_prometheus', {'promql': promql})


@server.tool(
    name="run_redis_command",
    description="在 Redis 上执行只读命令（INFO/DBSIZE/CONFIG GET/SLOWLOG 等，写命令一律拒绝）",
)
async def run_redis_command(command: str) -> str:
    return await _invoke('run_redis_command', {'command': command})


@server.tool(
    name="run_kafka_command",
    description="在 Kafka 容器内执行只读命令（consumer-groups --describe / topics --list）",
)
async def run_kafka_command(command: str) -> str:
    return await _invoke('run_kafka_command', {'subcommand': command})


async def _startup() -> None:
    await _load_descriptor()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="AIOps 运维工具 MCP Server")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "sse"])
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    async def run_all():
        await _startup()
        if args.transport == "sse":
            import uvicorn

            config = uvicorn.Config(server.sse_app(), host="127.0.0.1", port=args.port)
            await uvicorn.Server(config).serve()
        else:
            await server.run_stdio_async()

    asyncio.run(run_all())


if __name__ == "__main__":
    main()

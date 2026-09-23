"""AIOps 运维工具 MCP Server——把平台的只读运维能力暴露为 MCP 协议服务。

价值：
  * 任何 MCP 客户端（Claude Desktop / Cursor / 其他 Agent）都能直接使用这套运维工具
  * 新数据源接入从「改 Agent 代码」变为「注册一个 MCP 工具」
  * 平台自身的诊断 Agent 后续可切换为 MCP Client 动态发现工具（当前仍用原生工具集，
    避免 IPC 开销；见 AGENT_BRIEF「MCP 工具层」节）

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
import shlex
import subprocess
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
for p in (_here.parent.parent.parent, _here.parent.parent, _here):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mcp.server.mcpserver import MCPServer

from app.services.descriptors.builder import system_to_descriptor
from app.services.descriptors.health import (
    collect_health,
    read_service_logs,
    search_service_logs,
)

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
    log.info("已绑定系统 #%s %s", system_id, system.name)


async def _run_blocking(fn, *args, **kwargs):
    return await asyncio.get_event_loop().run_in_executor(None, lambda: fn(*args, **kwargs))


@server.tool(
    name="list_services",
    description="列出目标系统全部服务的健康状态（探活首选入手）",
)
async def list_services() -> str:
    health = await _run_blocking(collect_health, _descriptor())
    if not health:
        return "该系统未注册任何服务"
    return "\n".join(f"{i['name']}: {i['detail']} {'✅' if i['ok'] else '❌'}" for i in health)


@server.tool(
    name="health_check",
    description="检查单个服务的健康状态（探活 + 详情）",
)
async def health_check(service: str) -> str:
    for item in await _run_blocking(collect_health, _descriptor()):
        if item["name"] == service:
            return f"{service}: {item['detail']} {'✅' if item['ok'] else '❌'}"
    return f"系统中无服务「{service}」"


@server.tool(
    name="check_container_state",
    description="查容器真实状态：运行/退出原因/OOMKilled/退出码/内存上限/重启次数",
)
async def check_container_state(service: str) -> str:
    import subprocess

    d = _descriptor()
    svc = next((s for s in d.get("services", []) if s.get("name") == service), None)
    if not svc:
        return f"系统中无服务「{service}」"
    container = svc.get("container")
    if not container:
        return f"服务「{service}」不是容器化部署，无容器状态可查"
    proc = await _run_blocking(
        lambda: subprocess.run(
            ["docker", "inspect", container, "--format",
             "Status={{.State.Status}}|OOMKilled={{.State.OOMKilled}}|ExitCode={{.State.ExitCode}}"
             "|Restarts={{.RestartCount}}|Memory={{.HostConfig.Memory}}|FinishedAt={{.State.FinishedAt}}"],
            capture_output=True, text=True, timeout=15,
        )
    )
    if proc.returncode != 0:
        return f"docker inspect {container} 失败: {(proc.stderr or '').strip()[:200]}"
    return f"容器 {container}: {proc.stdout.strip()}"


@server.tool(
    name="read_logs",
    description="读取某服务最近 N 行日志",
)
async def read_logs(service: str, lines: int = 50) -> str:
    return await _run_blocking(read_service_logs, _descriptor(), service, max(1, min(int(lines), 300)))


@server.tool(
    name="search_logs",
    description="在某服务日志中按关键词搜索（最近 500 行内）",
)
async def search_logs(service: str, keyword: str, lines: int = 200) -> str:
    return await _run_blocking(
        search_service_logs, _descriptor(), service, keyword, max(1, min(int(lines), 500))
    )


@server.tool(
    name="query_prometheus",
    description="执行 PromQL 即时查询（最多返回 8 条序列）",
)
async def query_prometheus(promql: str) -> str:
    import httpx

    d = _descriptor()
    base = None
    for svc in d.get("services", []):
        if svc.get("connector") == "prometheus":
            url_cfg = svc.get("health_url") or svc.get("url") or ""
            base = url_cfg.replace("/metrics", "").rstrip("/")
    if not base:
        return "该系统未注册 Prometheus 服务"
    try:
        resp = httpx.get(f"{base}/api/v1/query", params={"query": promql}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            return f"Prometheus 返回错误: {data.get('error', data)}"
        results = data["data"]["result"]
        if not results:
            return f"查询 `{promql}` 无数据（指标可能不存在或目标未被抓取）"
        lines = []
        for r in results[:8]:
            metric = r.get("metric", {})
            labels = ", ".join(f'{k}="{v}"' for k, v in metric.items() if k not in ("__name__", "job"))
            name = metric.get("__name__", promql.split("{")[0])
            lines.append(f"{name}{'{' + labels + '}' if labels else ''} = {r['value'][1]}")
        note = f"（其余 {len(results) - 8} 条省略）" if len(results) > 8 else ""
        return f"PromQL: `{promql}` ({len(results)} 条){note}\n" + "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"Prometheus 查询失败: {exc}"


REDIS_READONLY = ("INFO", "DBSIZE", "CONFIG GET", "SLOWLOG", "SCAN", "CLIENT", "MEMORY", "GET")


@server.tool(
    name="run_redis_command",
    description="在 Redis 上执行只读命令（INFO/DBSIZE/CONFIG GET/SLOWLOG 等，写命令一律拒绝）",
)
async def run_redis_command(command: str) -> str:
    import subprocess

    d = _descriptor()
    svc = next((s for s in d.get("services", []) if s.get("name", "").upper() == "REDIS"), None)
    if not svc:
        return "该系统未注册 Redis 服务"
    upper = command.strip().upper()
    if not any(upper.startswith(a) for a in ("INFO", "DBSIZE", "CONFIG GET", "SLOWLOG", "KEYS", "SCAN", "CLIENT", "MEMORY")):
        return f"拒绝执行：仅放行只读命令（INFO/DBSIZE/CONFIG GET/SLOWLOG 等），收到: {command[:80]}"
    host, port = svc.get("host", "127.0.0.1"), int(svc.get("port", 6379))
    container = svc.get("container") or ""
    base = ["docker", "exec", container, "redis-cli"] if container else ["redis-cli"]
    cmd = base + ["-h", host, "-p", str(port), *shlex.split(command)]
    proc = await _run_blocking(
        lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    )
    if proc.returncode != 0:
        return f"Redis 命令执行失败: {(proc.stderr or '').strip()[:200]}"
    return "\n".join(proc.stdout.splitlines()[:80]) or proc.stdout[:500]


@server.tool(
    name="run_kafka_command",
    description="在 Kafka 容器内执行只读命令（consumer-groups --describe / topics --list）",
)
async def run_kafka_command(command: str) -> str:
    import subprocess

    d = _descriptor()
    svc = next((s for s in d.get("services", []) if s.get("name", "").upper() == "KAFKA"), None)
    if not svc:
        return "该系统未注册 Kafka 服务"
    container = svc.get("container") or ""
    if not container:
        return f"服务「{svc.get('name')}」未配置 Kafka 容器名"
    upper = command.strip().upper()
    if not (upper.startswith("CONSUMER-GROUPS") or upper.startswith("TOPICS --LIST")
            or upper.startswith("TOPICS --DESCRIBE")):
        return "拒绝执行：仅放行 CONSUMER-GROUPS --describe / TOPICS --list（只读白名单）"

    parts = shlex.split(command)
    if upper.startswith("CONSUMER-GROUPS"):
        script = "/opt/kafka/bin/kafka-consumer-groups.sh"
    else:
        script = "/opt/kafka/bin/kafka-topics.sh"
    args = [script, "--bootstrap-server", "localhost:9092", *parts[1:]]
    proc = await _run_blocking(
        lambda: subprocess.run(["docker", "exec", container, *args],
                               capture_output=True, text=True, timeout=30)
    )
    if proc.returncode != 0:
        return f"Kafka 命令执行失败: {(proc.stderr or '').strip()[:300]}"
    return "\n".join(proc.stdout.splitlines()[:80]) or proc.stdout[:500]


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

            uvicorn.run(server.sse_app(), host="0.0.0.0", port=args.port)
        else:
            await server.run_stdio_async()

    asyncio.run(run_all())


if __name__ == "__main__":
    main()

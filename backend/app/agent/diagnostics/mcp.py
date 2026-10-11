"""MCP（Model Context Protocol）接入：把第三方 MCP server 的只读工具接进诊断 agent。

设计取舍（与平台既有原则一致）：
  * **预定义、可审计**：不为每个 MCP 工具自动注册一个原生 tool（那样工具集会失控、
    且 agent 能间接获得任意能力），而是固定暴露两个工具 `mcp_list_tools` / `mcp_call`，
    先列后调，每一步都进证据链与审计。
  * **运维侧白名单**：只连 `MCP_SERVERS` 里显式配置的 server；不配置 = 能力关闭。
  * **输出视为外部数据**：MCP 返回内容由 `_untrusted` 护栏包裹，当数据不当指令。

配置（settings.mcp_servers / .env 里的 MCP_SERVERS）是一个 JSON 数组：
  [
    {"name": "k8s", "command": "{python}", "args": ["deploy/mcp/demo_k8s_server.py"],
     "description": "Kubernetes 集群只读查询（Pod/事件/日志）", "env": {}, "timeout": 30}
  ]
`{python}` 会被替换成当前解释器路径（便于用平台自带 venv 跑本地 MCP server）。
"""
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field

from ...core.config import settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MCPServer:
    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict = field(default_factory=dict)
    description: str = ""
    timeout: float = 30.0


def _subst(value: str) -> str:
    return str(value).replace("{python}", sys.executable).replace("{repo}", settings.repo_root)


def load_servers(raw: str | None = None) -> list[MCPServer]:
    """解析 MCP server 配置；非法配置忽略并记日志（不影响诊断主流程）。"""
    text = settings.mcp_servers if raw is None else raw
    text = (text or "").strip()
    if not text:
        return []
    try:
        items = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("[mcp] MCP_SERVERS 解析失败，已忽略: %s", exc)
        return []
    if not isinstance(items, list):
        log.warning("[mcp] MCP_SERVERS 必须是 JSON 数组")
        return []
    servers: list[MCPServer] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        command = str(item.get("command") or "").strip()
        if not name or not command:
            continue
        args = item.get("args") or []
        servers.append(
            MCPServer(
                name=name,
                command=_subst(command),
                args=tuple(_subst(a) for a in args),
                env={str(k): str(v) for k, v in (item.get("env") or {}).items()},
                description=str(item.get("description") or "").strip(),
                timeout=float(item.get("timeout") or 30),
            )
        )
    return servers


def get_server(name: str) -> MCPServer | None:
    wanted = (name or "").strip().lower()
    for server in load_servers():
        if server.name.lower() == wanted:
            return server
    return None


def catalog() -> str:
    """给系统提示用的 MCP server 清单（仅列 server，不预拉工具，避免启动即起进程）。"""
    servers = load_servers()
    if not servers:
        return ""
    lines = [
        "## 可用外部 MCP 工具（第三方系统，只读查询）",
        "下列 MCP server 已接入，可查询平台自身探活覆盖不到的外部系统。",
        "调用顺序：先 `mcp_list_tools(server)` 看该 server 提供哪些工具，再 `mcp_call(server, tool, arguments_json)`。",
    ]
    for server in servers:
        desc = f"：{server.description}" if server.description else ""
        lines.append(f"  - {server.name}{desc}")
    return "\n".join(lines)


def _params(server: MCPServer):
    from mcp.client.stdio import StdioServerParameters

    return StdioServerParameters(
        command=server.command,
        args=list(server.args),
        env={**os.environ, **server.env},
    )


async def _list_tools(server: MCPServer) -> list[dict]:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(_params(server)) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=server.timeout)
            result = await asyncio.wait_for(session.list_tools(), timeout=server.timeout)
            return [
                {
                    "name": t.name,
                    "description": (t.description or "").strip(),
                    # mcp 2.x 用 input_schema；1.x 用 inputSchema。都兼容一下。
                    "schema": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None),
                }
                for t in result.tools
            ]


async def _call_tool(server: MCPServer, tool: str, arguments: dict) -> str:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(_params(server)) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=server.timeout)
            result = await asyncio.wait_for(
                session.call_tool(tool, arguments), timeout=server.timeout
            )
            chunks: list[str] = []
            for block in result.content or []:
                text = getattr(block, "text", None)
                if text:
                    chunks.append(text)
                else:
                    chunks.append(f"[{getattr(block, 'type', 'content')}]")
            body = "\n".join(chunks) or "(空结果)"
            if getattr(result, "isError", False):
                return f"MCP 工具返回错误：{body}"
            return body


def list_tools_text(server_name: str) -> str:
    server = get_server(server_name)
    if not server:
        names = "、".join(s.name for s in load_servers()) or "（未配置任何 MCP server）"
        return f"未找到 MCP server「{server_name}」。已配置：{names}"
    try:
        tools = asyncio.run(asyncio.wait_for(_list_tools(server), timeout=server.timeout + 10))
    except Exception as exc:  # noqa: BLE001
        log.warning("[mcp] 列工具失败 server=%s: %s", server.name, exc)
        return f"连接 MCP server「{server.name}」失败：{exc}"
    if not tools:
        return f"MCP server「{server.name}」没有提供任何工具。"
    lines = [f"MCP server「{server.name}」提供 {len(tools)} 个工具："]
    for tool in tools:
        desc = f" —— {tool['description']}" if tool["description"] else ""
        lines.append(f"  - {tool['name']}{desc}")
    return "\n".join(lines)


def call_tool_text(server_name: str, tool: str, arguments_json: str = "{}") -> str:
    server = get_server(server_name)
    if not server:
        return f"未找到 MCP server「{server_name}」，请先用 mcp_list_tools 查看可用 server。"
    try:
        arguments = json.loads(arguments_json) if (arguments_json or "").strip() else {}
    except Exception as exc:  # noqa: BLE001
        return f"arguments_json 不是合法 JSON：{exc}"
    if not isinstance(arguments, dict):
        return "arguments_json 必须是 JSON 对象（如 {\"namespace\": \"default\"}）。"
    try:
        text = asyncio.run(
            asyncio.wait_for(_call_tool(server, tool, arguments), timeout=server.timeout + 10)
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[mcp] 调用失败 server=%s tool=%s: %s", server.name, tool, exc)
        return f"调用 MCP 工具失败（server={server.name} tool={tool}）：{exc}"
    return text

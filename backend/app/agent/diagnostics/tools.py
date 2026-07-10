"""Tooling and runtime helpers for the diagnosis agent."""
import shlex
import socket
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from pydantic_ai import Agent, RunContext

from ...services.descriptors.health import collect_health, read_service_logs, search_service_logs
from ...services.descriptors.prompt import build_prompt
from .knowledge.store import get_relevant_context
from .skill_router import get_skill_steps


@dataclass
class AgentDeps:
    descriptor: dict
    question: str = ""
    skill_steps: str | None = None


def register_tools(agent: Agent) -> Agent:
    @agent.system_prompt
    def system_prompt(ctx: RunContext[AgentDeps]) -> str:
        base = build_prompt(ctx.deps.descriptor)
        skill_steps = (
            get_skill_steps(ctx.deps.question)
            if ctx.deps.skill_steps is None
            else ctx.deps.skill_steps
        )
        return f"{base}\n\n{skill_steps}" if skill_steps else base

    @agent.tool
    def list_services(ctx: RunContext[AgentDeps]) -> str:
        health = collect_health(ctx.deps.descriptor)
        if not health:
            return "该系统未注册任何服务"
        return "\n".join(
            f"  {item['name']}: {item['detail']} {'✅' if item['ok'] else '❌'}" for item in health
        )

    @agent.tool
    def check_service(ctx: RunContext[AgentDeps], service: str) -> str:
        for item in collect_health(ctx.deps.descriptor):
            if item["name"] == service:
                return f"{service}: {item['detail']} {'✅' if item['ok'] else '❌'}"
        return f"系统中无服务「{service}」"

    @agent.tool
    def read_logs(ctx: RunContext[AgentDeps], service: str, lines: int = 50) -> str:
        return read_service_logs(ctx.deps.descriptor, service, lines)

    @agent.tool
    def search_logs(ctx: RunContext[AgentDeps], service: str, keyword: str, lines: int = 200) -> str:
        return search_service_logs(ctx.deps.descriptor, service, keyword, lines)

    @agent.tool
    def query_prometheus(ctx: RunContext[AgentDeps], promql: str) -> str:
        base_url = find_prometheus_url(ctx.deps.descriptor)
        if not base_url:
            return "未找到 Prometheus 地址。请在服务列表中注册一个 health_url 含 9090 的 HTTP 服务，或在 infra.prometheus_url 中配置。"
        try:
            response = httpx.get(
                f"{base_url}/api/v1/query",
                params={"query": promql},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") != "success":
                return f"Prometheus 返回错误: {data.get('error', data)}"
            results = data["data"]["result"]
            if not results:
                return f"查询 `{promql}` 无数据（指标可能不存在或目标未被抓取）"
            lines = []
            for result in results[:20]:
                metric = result.get("metric", {})
                labels = ", ".join(f'{k}="{v}"' for k, v in metric.items() if k != "__name__")
                name = metric.get("__name__", promql.split("{")[0])
                value = result["value"][1]
                lines.append(f"  {name}{'{' + labels + '}' if labels else ''} = {value}")
            header = f"PromQL: `{promql}`  ({len(results)} 条结果)"
            return header + "\n" + "\n".join(lines)
        except Exception as exc:
            return f"Prometheus 查询失败: {exc}"

    @agent.tool
    def run_kafka_command(ctx: RunContext[AgentDeps], subcommand: str) -> str:
        parts = shlex.split(subcommand)
        if not parts or parts[0] not in ("topics", "consumer-groups"):
            return "安全限制：只允许 topics 或 consumer-groups 子命令"
        dangerous = {"--create", "--delete", "--alter", "--reset-offsets", "--execute"}
        if any(part in dangerous for part in parts):
            return f"安全限制：拒绝写操作 {[part for part in parts if part in dangerous]}"

        container = None
        for candidate in ("ai-ops-agent-kafka-1", "kafka"):
            inspect = subprocess.run(["docker", "inspect", candidate], capture_output=True, timeout=5)
            if inspect.returncode == 0:
                container = candidate
                break
        if not container:
            return "未找到 Kafka 容器（ai-ops-agent-kafka-1），请确认容器正在运行"

        script = f"kafka-{parts[0]}.sh"
        command = ["docker", "exec", container, f"/opt/kafka/bin/{script}", "--bootstrap-server", "localhost:9092"] + parts[1:]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            output = (result.stdout + result.stderr).strip()
            return output[:3000] if output else "(无输出)"
        except subprocess.TimeoutExpired:
            return "命令超时（30s）"
        except Exception as exc:
            return f"执行失败: {exc}"

    @agent.tool
    def run_redis_command(ctx: RunContext[AgentDeps], command: str) -> str:
        first = command.strip().upper().split()[0]
        if first not in {"INFO", "DBSIZE", "CLIENT", "CONFIG", "SLOWLOG", "KEYS", "TTL", "TYPE", "LLEN", "SCARD", "ZCARD", "HLEN", "STRLEN", "OBJECT"}:
            return f"安全限制：拒绝执行写命令「{first}」，仅允许只读命令。"

        host, port = find_redis_addr(ctx.deps.descriptor)
        if not host:
            return "未找到 Redis 服务（需要 connector=tcp 且 port=6379 的服务）"

        try:
            command_parts = command.strip().split()
            resp_command = f"*{len(command_parts)}\r\n" + "".join(
                f"${len(part)}\r\n{part}\r\n" for part in command_parts
            )
            with socket.create_connection((host, int(port)), timeout=5) as conn:
                conn.sendall(resp_command.encode())
                chunks = []
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if len(chunk) < 65536:
                        break
            raw = b"".join(chunks).decode("utf-8", errors="replace")
            lines = [line for line in raw.splitlines() if not line.startswith(("*", "$", ":")) or " " in line]
            return "\n".join(lines[:80]) or raw[:500]
        except Exception as exc:
            return f"Redis 命令执行失败: {exc}"

    @agent.tool
    def search_knowledge_base(ctx: RunContext[AgentDeps], query: str) -> str:
        """从该系统的运维知识库检索历史故障经验、操作手册、系统文档。
        遇到不熟悉的故障类型、需要参考历史处理经验或查找操作步骤时调用。
        返回语义相关的文档片段；知识库为空或无匹配时返回空字符串。"""
        system_id = ctx.deps.descriptor.get("id", "default")
        context = get_relevant_context(query, system_id)
        if not context:
            return "知识库中暂无相关记录。"
        return f"## 知识库检索结果\n\n{context}"

    return agent


def find_prometheus_url(descriptor: dict) -> str:
    infra = descriptor.get("infra", {})
    if infra.get("prometheus_url"):
        return infra["prometheus_url"].rstrip("/")
    for service in descriptor.get("services", []):
        config = service.get("config", {})
        url = (
            service.get("url", "")
            or config.get("url", "")
            or service.get("health_url", "")
            or config.get("health_url", "")
        )
        if service.get("connector") == "prometheus" and url:
            return url.rstrip("/")
        if url and ("9090" in url or "prometheus" in url.lower()):
            parsed = urlparse(url)
            return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def find_redis_addr(descriptor: dict) -> tuple[str, int]:
    for service in descriptor.get("services", []):
        config = service.get("config", {})
        name = service.get("name", "").lower()
        if int(config.get("port", 0)) == 6379 or "redis" in name:
            return config.get("host", "127.0.0.1"), int(config.get("port", 6379))
    return "", 6379

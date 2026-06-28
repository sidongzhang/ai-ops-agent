"""
诊断 Agent —— 用 Pydantic AI 重写原手搓 ReAct 循环。
- 工具由类型签名自动生成 schema（不再手写 JSON）
- 租户/系统上下文经 deps 依赖注入（不再手动透传 system_id）
- 动态系统提示来自注册描述符（拓扑非硬编码）
- 模型可一行切换（DeepSeek 分诊 / Claude 硬核诊断）
"""
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from ..config import settings
from ..descriptors import build_prompt, collect_health, read_service_logs, search_service_logs


@dataclass
class AgentDeps:
    """注入给每个工具的运行时上下文：当前系统的连接器描述符。"""
    descriptor: dict


def _build_model() -> OpenAIChatModel:
    return OpenAIChatModel(
        settings.agent_model,
        provider=OpenAIProvider(base_url=settings.deepseek_base_url,
                                api_key=settings.deepseek_api_key),
    )


diagnose_agent = Agent(_build_model(), deps_type=AgentDeps)


@diagnose_agent.system_prompt
def _system_prompt(ctx: RunContext[AgentDeps]) -> str:
    return build_prompt(ctx.deps.descriptor)


@diagnose_agent.tool
def list_services(ctx: RunContext[AgentDeps]) -> str:
    """列出当前系统所有已注册服务的健康状态。"""
    health = collect_health(ctx.deps.descriptor)
    if not health:
        return "该系统未注册任何服务"
    return "\n".join(
        f"  {h['name']}: {h['detail']} {'✅' if h['ok'] else '❌'}" for h in health
    )


@diagnose_agent.tool
def check_service(ctx: RunContext[AgentDeps], service: str) -> str:
    """检查单个服务的健康状态。service 为已注册的服务名。"""
    for h in collect_health(ctx.deps.descriptor):
        if h["name"] == service:
            return f"{service}: {h['detail']} {'✅' if h['ok'] else '❌'}"
    return f"系统中无服务「{service}」"


@diagnose_agent.tool
def read_logs(ctx: RunContext[AgentDeps], service: str, lines: int = 50) -> str:
    """读取某服务最新日志（最后 N 行）。"""
    return read_service_logs(ctx.deps.descriptor, service, lines)


@diagnose_agent.tool
def search_logs(ctx: RunContext[AgentDeps], service: str, keyword: str, lines: int = 200) -> str:
    """在某服务日志中搜索关键词（如 ERROR、Exception）。"""
    return search_service_logs(ctx.deps.descriptor, service, keyword, lines)


def diagnose(descriptor: dict, question: str) -> str:
    result = diagnose_agent.run_sync(question, deps=AgentDeps(descriptor=descriptor))
    return result.output

"""
诊断 Agent —— Pydantic AI + Langfuse 可观测性 + 关键词驱动模型路由。

模型路由策略：
  普通问题 → agent_model（DeepSeek Chat，快且便宜）
  高危/复杂 → advanced_agent_model（DeepSeek Reasoner 或任意 OpenAI 兼容高级模型）
  触发词：P0 / 崩溃 / 宕机 / 数据丢失 / 根因分析 / 紧急 / 生产故障

Langfuse 追踪：
  LANGFUSE_PUBLIC_KEY 未配置时跳过，零摩擦 dev 模式。
  每次诊断记录：问题、回答、模型、token 用量、耗时、org/system 元数据。
"""
import logging
import time
from dataclasses import dataclass
from typing import Optional

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from ..config import settings
from ..descriptors import build_prompt, collect_health, read_service_logs, search_service_logs

log = logging.getLogger(__name__)

# 触发高级模型的关键词（出现任意一个即升档）
_ADVANCED_KEYWORDS = {
    "P0", "p0", "崩溃", "宕机", "数据丢失", "根因分析",
    "紧急", "生产故障", "无法访问", "大面积", "所有服务",
}


@dataclass
class AgentDeps:
    """注入给每个工具的运行时上下文：当前系统的连接器描述符。"""
    descriptor: dict


def _make_model(model_name: str, base_url: str, api_key: str) -> OpenAIChatModel:
    return OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(base_url=base_url, api_key=api_key),
    )


def _default_model() -> OpenAIChatModel:
    return _make_model(
        settings.agent_model,
        settings.deepseek_base_url,
        settings.deepseek_api_key,
    )


def _advanced_model() -> OpenAIChatModel:
    return _make_model(
        settings.advanced_agent_model,
        settings.advanced_agent_base_url or settings.deepseek_base_url,
        settings.advanced_agent_api_key or settings.deepseek_api_key,
    )


def _pick_model(question: str) -> OpenAIChatModel:
    """关键词路由：检测高危/复杂信号则升级模型。"""
    if settings.advanced_agent_model and any(kw in question for kw in _ADVANCED_KEYWORDS):
        log.info(f"[model-routing] 升档至高级模型 {settings.advanced_agent_model!r}")
        return _advanced_model()
    return _default_model()


# Agent 单例（工具绑定在上面；实际调用时通过 model 参数覆盖模型）
diagnose_agent = Agent(_default_model(), deps_type=AgentDeps)


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


# ── Langfuse 懒加载（未配置时 None）─────────────────────────────
def _get_langfuse():
    if not settings.langfuse_public_key:
        return None
    try:
        from langfuse import Langfuse
        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except Exception as e:
        log.warning(f"[langfuse] 初始化失败（跳过追踪）: {e}")
        return None


def diagnose(
    descriptor: dict,
    question: str,
    org_id: int = 0,
    system_id: int = 0,
) -> str:
    """运行诊断 Agent，自动路由模型，按需追踪到 Langfuse。"""
    model = _pick_model(question)
    model_name = model.model_name if hasattr(model, "model_name") else str(model)
    lf = _get_langfuse()
    trace = generation = None

    if lf:
        trace = lf.trace(
            name="diagnose",
            input=question,
            metadata={
                "org_id": org_id,
                "system_id": system_id,
                "system_name": descriptor.get("name", ""),
                "model": model_name,
            },
        )
        generation = trace.generation(
            name="pydantic-ai",
            model=model_name,
            input=question,
        )

    t0 = time.monotonic()
    try:
        result = diagnose_agent.run_sync(
            question,
            deps=AgentDeps(descriptor=descriptor),
            model=model,
        )
        answer = result.output
        usage = result.usage()

        if generation:
            generation.end(
                output=answer,
                usage={
                    "input": usage.request_tokens or 0,
                    "output": usage.response_tokens or 0,
                },
            )
        if trace:
            trace.update(output=answer)

        log.info(
            f"[diagnose] system={system_id} model={model_name} "
            f"tokens={usage.total_tokens} elapsed={time.monotonic()-t0:.2f}s"
        )
        return answer

    except Exception as e:
        if generation:
            generation.end(level="ERROR", status_message=str(e))
        raise
    finally:
        if lf:
            lf.flush()

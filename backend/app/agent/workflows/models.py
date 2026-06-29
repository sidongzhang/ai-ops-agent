"""Structured models for workflow analysis."""
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from ...core.config import settings


class ProposedAction(BaseModel):
    type: Literal["fetch_logs", "health_check", "manual"]
    description: str
    service: str = ""
    args: dict = Field(default_factory=dict)
    manual_steps: list[str] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    diagnosis: str
    proposed_action: ProposedAction


@dataclass
class AnalysisDeps:
    descriptor: dict


def make_analysis_model() -> OpenAIChatModel:
    return OpenAIChatModel(
        settings.agent_model,
        provider=OpenAIProvider(
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key,
        ),
    )


analysis_agent = Agent(
    make_analysis_model(),
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
def inject_descriptor(ctx: RunContext[AnalysisDeps]) -> str:
    import json

    return f"\n\n系统描述（JSON）：\n{json.dumps(ctx.deps.descriptor, ensure_ascii=False, indent=2)}"

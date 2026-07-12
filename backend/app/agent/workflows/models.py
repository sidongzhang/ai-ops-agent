"""Structured models for workflow analysis."""
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from ...core.config import settings


class ProposedAction(BaseModel):
    type: Literal["fetch_logs", "health_check", "restart_container", "restart_systemd", "run_redis_command", "manual"]
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
- fetch_logs        ：拉取服务日志排查；service 填服务名，args 可加 {"lines": 100}
- health_check      ：验证服务是否恢复正常；service 填服务名
- restart_container ：重启 Docker 容器（服务崩溃/无响应时使用）；必须同时填写 service 和 args={"container": "容器名"}
                      容器名只能使用系统描述里该服务的 config.container，如 ai-ops-agent-redis-1
                      本地系统会在平台宿主机执行；远程系统会通过采集器执行
- restart_systemd    ：重启已注册的 systemd 服务；必须同时填写 service 和 args={"unit": "服务名.service"}
                      仅允许重启系统描述中该服务已登记的 systemd_unit
- run_redis_command ：在 Redis 上执行特定命令；args 填 {"command": "FLUSHDB"} 或 {"command": "CONFIG SET maxmemory 256mb"}
                      本地审批工作流可执行受控运维命令；远程采集器仅执行只读查询
- manual            ：需要人工执行的操作；manual_steps 列出具体步骤

优先级：能自动修复 > 日志排查 > 人工操作。
重启容器风险低且可逆，优先考虑；数据清理需谨慎，选 manual 说明风险。""",
)


@analysis_agent.system_prompt
def inject_descriptor(ctx: RunContext[AnalysisDeps]) -> str:
    import json

    return f"\n\n系统描述（JSON）：\n{json.dumps(ctx.deps.descriptor, ensure_ascii=False, indent=2)}"

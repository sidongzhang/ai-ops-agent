"""取证子代理（DeepAgents 式上下文隔离）。

把多步取证隔离在独立上下文窗口里执行：子代理自己调用工具、自己消化原始
日志/指标输出，只把紧凑的证据摘要交回主代理。主代理上下文不装原始证据，
从结构上消除「每多一轮工具调用，整段上下文重发一次」的线性膨胀。

与主代理的关系：
  * 工具集 = 主代理的取证工具子集（evidence_only 注册），无知识库/业务数据/委派工具
  * 共享同一个 tool_cache —— 子代理查过的数据主代理侧不会重复取证
  * 系统提示极简（不装剧本/知识库），上下文起点小
"""
import logging

from pydantic_ai import Agent

from .tools import AgentDeps, register_tools

log = logging.getLogger(__name__)

_evidence_agent: Agent | None = None

INVESTIGATOR_PROMPT = """你是取证子代理，为运维诊断收集证据。

规则：
- 按任务直接调用取证工具（健康检查/容器状态/日志/PromQL/Redis/Kafka），互不依赖的调用放同一轮批量发出。
- 取证深度由你把握：证据足够支撑结论即停止，不要为了完整性遍历所有服务。
- 输出格式（强制）：
  结论: <一句话判断>
  证据:
  - <工具返回的原文片段或数值>（≤5 条，每条 ≤80 字符）
  未确认: <拿不到的信息，一行；没有则整行省略>
- 禁止：修复建议、长篇分析、客套话、复述过程。只回证据摘要。"""


def get_evidence_agent() -> Agent:
    """进程级单例：取证子代理构建一次复用（模型与主代理一致）。"""
    global _evidence_agent
    if _evidence_agent is None:
        from .models import default_model

        _evidence_agent = Agent(
            default_model(),
            deps_type=AgentDeps,
            instructions=INVESTIGATOR_PROMPT,
        )
        register_tools(_evidence_agent, evidence_only=True)
        log.info("[investigator] 取证子代理已就绪（8 个取证工具，独立上下文）")
    return _evidence_agent

"""专家分工（多智能体 MVP）：把复杂排查**并行**派发给若干"取证专家"子代理，汇总各自结论。

专家 = 复用取证子代理（独立上下文），但用**角色化指令**聚焦某一类证据：
  - infra    ：服务/容器健康、端口可达、重启状态
  - metrics  ：Prometheus 指标、资源水位、消息积压
  - logs     ：日志报错、堆栈、慢查询
  - cache_mq ：Redis / Kafka 只读状态

并行执行（asyncio），各自上下文隔离，只回紧凑摘要；任一专家失败不影响其他。
"""
import logging

from .tools import AgentDeps

log = logging.getLogger(__name__)

ROLE_FOCUS = {
    "infra": "只关注服务与容器健康、端口可达、重启状态（list_services / check_service / check_container_state）。",
    "metrics": "只关注 Prometheus 指标与资源水位（query_prometheus：up、CPU/内存、Kafka lag 等）。",
    "logs": "只关注日志里的报错/堆栈/慢查询（read_logs / search_logs）。",
    "cache_mq": "只关注 Redis 与 Kafka 的只读状态（run_redis_command / run_kafka_command）。",
}

ROLE_ORDER = ("infra", "metrics", "logs", "cache_mq")


async def run_specialists(deps: AgentDeps, task: str, roles=None, *, model=None, context=None) -> dict[str, str]:
    """并行运行专家子代理（async，须在异步工具内 await）。model 缺省时回退默认模型。"""
    import asyncio

    from .investigator import get_evidence_agent
    from .models import default_model

    agent = get_evidence_agent()
    selected = [r for r in (roles or []) if r in ROLE_FOCUS] or list(ROLE_ORDER)
    run_model = model or default_model()

    async def _one(role: str) -> tuple[str, str]:
        from ..harness.delegation import child_context
        from ..harness.catalog import EVIDENCE_TOOLS
        from .tool_events import nested_event_handler
        from .tool_adapter import child_run_options
        from types import SimpleNamespace
        sub = child_context(deps, task, EVIDENCE_TOOLS)
        sub.performance_agent = f"expert_{role}_{sub.performance_agent}"
        parent = context or SimpleNamespace(deps=deps)
        prompt = f"角色：{role} 专家。{ROLE_FOCUS[role]}\n任务：{task}"
        try:
            # 2.55 起禁止在同步上下文里嵌套 run_sync（防死锁），统一 async + await
            result = await agent.run(
                prompt, deps=sub, model=run_model, retries=1,
                event_stream_handler=nested_event_handler(sub),
                **child_run_options(parent),
            )
            return role, str(result.output).strip()[:1500]
        except deps.control_flow_errors:
            raise
        except Exception as exc:  # noqa: BLE001 - 单个专家失败不影响整体
            deps.usage_gaps.append('specialist_failed_attempt')
            log.debug("[specialists] %s 专家失败: %s", role, exc)
            return role, f"（该专家取证失败：{exc}）"

    async def _gather() -> dict[str, str]:
        tasks = [asyncio.create_task(_one(role)) for role in dict.fromkeys(selected)]
        try:
            return dict(await asyncio.gather(*tasks))
        except BaseException:
            for child in tasks:
                if not child.done():
                    child.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    return await _gather()

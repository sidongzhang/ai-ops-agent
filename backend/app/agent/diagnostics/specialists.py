"""专家分工（多智能体 MVP）：把复杂排查**并行**派发给若干"取证专家"子代理，汇总各自结论。

专家 = 复用取证子代理（独立上下文），但用**角色化指令**聚焦某一类证据：
  - infra    ：服务/容器健康、端口可达、重启状态
  - metrics  ：Prometheus 指标、资源水位、消息积压
  - logs     ：日志报错、堆栈、慢查询
  - cache_mq ：Redis / Kafka 只读状态

并行执行（ThreadPoolExecutor），各自上下文隔离，只回紧凑摘要；任一专家失败不影响其他。
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


async def run_specialists(deps: AgentDeps, task: str, roles=None, *, model=None) -> dict[str, str]:
    """并行运行专家子代理（async，须在异步工具内 await）。model 缺省时回退默认模型。"""
    import asyncio

    from .investigator import get_evidence_agent
    from .models import default_model

    agent = get_evidence_agent()
    selected = [r for r in (roles or []) if r in ROLE_FOCUS] or list(ROLE_ORDER)
    run_model = model or default_model()

    async def _one(role: str) -> tuple[str, str]:
        sub = AgentDeps(
            descriptor=deps.descriptor,
            question=task,
            remote_command=deps.remote_command,
            business_data_query=deps.business_data_query,
            business_dataset_query=deps.business_dataset_query,
            # 专家子代理也要拿到知识库（否则"并行派专家"这条路同样看不到文档）
            knowledge_context=deps.knowledge_context,
            clock_hint=deps.clock_hint,
            tool_cache=deps.tool_cache,
            cancel_token=deps.cancel_token,
        )
        prompt = f"角色：{role} 专家。{ROLE_FOCUS[role]}\n任务：{task}"
        try:
            # 2.55 起禁止在同步上下文里嵌套 run_sync（防死锁），统一 async + await
            result = await agent.run(
                prompt, deps=sub, model=run_model, retries=1,
                cancellation_token=deps.cancel_token,
            )
            return role, str(result.output).strip()[:1500]
        except Exception as exc:  # noqa: BLE001 - 单个专家失败不影响整体
            log.debug("[specialists] %s 专家失败: %s", role, exc)
            return role, f"（该专家取证失败：{exc}）"

    async def _gather() -> dict[str, str]:
        pairs = await asyncio.gather(*(_one(role) for role in selected))
        return dict(pairs)

    return await _gather()

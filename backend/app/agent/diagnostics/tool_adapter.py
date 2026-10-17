"""Pydantic registration only; domain gateway is the shared execution boundary."""
import asyncio
import functools
import inspect

from pydantic_ai import Agent, RunContext

from ..harness.catalog import tool_catalog
from ..harness.gateway import ToolGateway, ToolCancelled


def child_run_options(ctx):
    """Use the framework's parent usage/limits rather than a fresh child budget."""
    from .request_control import ModelRequestGuard
    budget=getattr(ctx.deps,'request_budget',None)
    return {'usage': getattr(ctx, 'usage', None), 'usage_limits': getattr(ctx, 'usage_limits', None),
            'cancellation_token': ctx.deps.cancel_token,
            'capabilities':[ModelRequestGuard(budget)] if budget is not None else []}


def bind_tool(spec):
    @functools.wraps(spec.implementation)
    async def wrapper(ctx, *args, **kwargs):
        bound = inspect.signature(spec.implementation).bind(ctx, *args, **kwargs)
        bound.apply_defaults()
        arguments = {key: value for key, value in bound.arguments.items() if key != 'ctx'}
        try:
            result = await ToolGateway().invoke(spec.name, arguments, ctx, spec=spec)
        except ToolCancelled:
            # Native framework cancellation retains its partial-history semantics.
            ctx.deps.cancel_token.cancel()
            await asyncio.sleep(0)
            raise asyncio.CancelledError()
        return result.text
    return wrapper


def register_tools(agent: Agent, *, evidence_only: bool = False) -> Agent:
    from .tools import AgentDeps, build_system_prompt

    @agent.system_prompt
    def system_prompt(ctx: RunContext[AgentDeps]) -> str:
        return build_system_prompt(ctx.deps)

    for spec in tool_catalog():
        if evidence_only and not spec.evidence_only:
            continue
        agent.tool(bind_tool(spec))
    return agent

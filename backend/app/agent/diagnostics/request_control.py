"""Bind domain request admission to Pydantic's official model-request hook."""
import asyncio
from pydantic_ai.capabilities import AbstractCapability


class ModelRequestGuard(AbstractCapability):
    def __init__(self, budget):
        self.budget=budget
        self._request_tasks=set()

    async def for_run(self, ctx):
        return ModelRequestGuard(self.budget)

    async def wrap_run(self, ctx, *, handler):
        try:
            return await handler()
        finally:
            current=asyncio.current_task()
            pending=[task for task in self._request_tasks if task is not current]
            for task in pending:
                if not task.done():
                    task.cancel()
            if pending:
                await asyncio.gather(*pending,return_exceptions=True)

    def _finished(self, task):
        self._request_tasks.discard(task)
        if not task.cancelled():
            # Streaming handoff can be cancelled after an admission failure but
            # before the SDK consumer awaits it. Retrieve that owned task's
            # exception; awaiting the task still raises the same control error.
            task.exception()

    async def wrap_model_request(self, ctx, *, request_context, handler):
        task=asyncio.current_task()
        if task is not None and task not in self._request_tasks:
            self._request_tasks.add(task)
            task.add_done_callback(self._finished)
        from ..harness.performance import observer, current
        rec=observer(ctx.deps) or current()
        from types import SimpleNamespace
        performance_deps=ctx.deps or SimpleNamespace(request_budget=self.budget,tool_budget=getattr(self.budget,'tool_budget',None))
        if rec: rec.budget(performance_deps)
        span=rec.begin('model',name=str(getattr(request_context.model,'model_name','unknown')),
            agent=getattr(ctx.deps,'performance_agent','main'),parent=getattr(ctx.deps,'performance_parent',None)) if rec else None
        if rec: rec.update(span,admitted=False)
        status='success'
        try:
            self.budget.admit()
            if rec: rec.update(span,admitted=True)
            return await handler(request_context)
        except BaseException as exc:
            status='cancelled' if type(exc).__name__ in ('RunCancelled','CancelledError') else 'failed'
            raise
        finally:
            if rec:
                rec.budget(performance_deps)
                rec.end(span,status)

"""In-process ToolGateway. No network authentication or durable execution implied.

Cancelling a synchronous connector stops waiting, not its underlying thread. Such
connectors retain their native I/O deadlines; the gateway never retries them.
"""
import asyncio
import inspect
import logging
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from uuid import uuid4

from pydantic import ValidationError

from ...services.descriptors.ops_policy import kafka_command_denied, redis_command_denied

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunScope:
    org_id: int = 0
    system_id: int = 0
    run_id: str = ''
    actor_id: str = ''


class ToolAuditError(RuntimeError):
    """Audit could not be persisted; do not automatically repeat tool execution."""


class ToolCancelled(Exception):
    """Control flow: engine adapters translate this to their native cancellation."""


@dataclass(frozen=True)
class ToolResult:
    text: str
    status: str
    error_code: str = ''


def authorize(spec, ctx, arguments):
    deps = ctx.deps
    services = deps.descriptor.get('services', [])
    service = arguments.get('service')
    if service and service not in {s.get('name') for s in services}:
        return '服务未在当前系统登记，拒绝访问'
    if spec.name == 'run_kafka_command':
        denied = kafka_command_denied(arguments['subcommand'])
        if denied:
            return denied
        if not any('kafka' in s.get('name', '').lower() or s.get('connector') == 'kafka' for s in services):
            return '当前系统未登记 Kafka 服务，拒绝访问'
    if spec.name == 'run_redis_command':
        denied = redis_command_denied(arguments['command'])
        if denied:
            return denied
        if not any('redis' in s.get('name', '').lower() or str(s.get('config', {}).get('port', s.get('port', ''))) == '6379' for s in services):
            return '当前系统未登记 Redis 服务，拒绝访问'
    return ''


class ToolGateway:
    async def invoke(self, name, arguments, ctx, *, expected_scope=None, spec=None):
        from .catalog import tool_catalog

        spec = spec or next((s for s in tool_catalog() if s.name == name), None)
        scope = getattr(ctx.deps, 'scope', None) or RunScope()
        call_id = getattr(ctx.deps, 'tool_call_namespace', '') + (getattr(ctx, 'tool_call_id', '') or uuid4().hex)
        began = time.monotonic()
        from .performance import CALL, observer, implementation
        rec=observer(ctx.deps)
        if rec: rec.budget(ctx.deps)
        span=rec.begin('tool',name=name,call_id=call_id,arguments=arguments,
            agent=getattr(ctx.deps,'performance_agent','main'),parent=getattr(ctx.deps,'performance_parent',None)) if rec else None
        if rec: rec.update(span,budget_admitted=None if getattr(ctx.deps,'tool_budget',None) is None else False)
        result = None
        health_observation = None
        try:
            budget = getattr(ctx.deps, 'tool_budget', None)
            if budget is not None and (expected_scope is None or expected_scope == scope):
                budget.admit()
                if rec: rec.update(span,budget_admitted=True)
            if expected_scope is not None and expected_scope != scope:
                result = ToolResult('工具访问被拒绝：调用身份与运行身份不一致', 'denied', 'scope_mismatch')
            elif spec is None or spec.name != name:
                result = ToolResult('工具访问被拒绝：未登记工具', 'denied', 'unknown_tool')
            elif getattr(ctx.deps, 'allowed_tools', None) is not None and name not in ctx.deps.allowed_tools:
                result = ToolResult('工具访问被拒绝：不在当前代理的工具许可范围内', 'denied', 'tool_not_allowed')
            else:
                try:
                    payload = spec.input_model.model_validate(arguments).model_dump()
                except ValidationError:
                    result = ToolResult('工具参数无效：类型、必填字段或额外字段不符合目录', 'error', 'invalid_arguments')
                else:
                    denied = authorize(spec, ctx, payload)
                    if denied:
                        result = ToolResult(f'工具访问被拒绝：{denied}', 'denied', 'policy_denied')
                    elif getattr(getattr(ctx.deps, 'cancel_token', None), 'cancelled', False):
                        raise ToolCancelled('运行已取消，不再执行工具')
                    else:
                        def sync_execute():
                            binding=CALL.set((rec,span,call_id))
                            try:
                                if getattr(spec.implementation,'_performance_memoized',False):
                                    return spec.implementation(ctx, **payload)
                                with implementation(ctx.deps,name):
                                    return spec.implementation(ctx, **payload)
                            finally: CALL.reset(binding)
                        async def execute():
                            if inspect.iscoroutinefunction(spec.implementation):
                                binding=CALL.set((rec,span,call_id))
                                try:
                                    with implementation(ctx.deps,name):
                                        return await spec.implementation(ctx, **payload)
                                finally: CALL.reset(binding)
                            tracker = getattr(ctx.deps, 'execution_tracker', None)
                            if tracker is None:
                                return await asyncio.to_thread(sync_execute)
                            future = tracker.submit(call_id, sync_execute)
                            return await asyncio.wrap_future(future)
                        sink = getattr(ctx.deps, 'tool_audit_sink', None)
                        if sink:
                            try:
                                sink({'tool': name, 'input': arguments, 'call_id': call_id,
                                      'scope': {'org_id': scope.org_id, 'system_id': scope.system_id, 'run_id': scope.run_id},
                                      'status': 'started', 'error_code': '', 'duration_ms': 0})
                            except Exception as exc:
                                raise ToolAuditError('工具审计写入失败，未执行工具') from exc
                        try:
                            task = asyncio.create_task(execute())
                            try:
                                async def wait_result():
                                    while not task.done():
                                        if getattr(getattr(ctx.deps, 'cancel_token', None), 'cancelled', False):
                                            raise ToolCancelled('运行已取消，停止等待工具')
                                        await asyncio.wait({task}, timeout=0.05)
                                    return await task
                                text = await asyncio.wait_for(wait_result(), spec.timeout_seconds)
                            finally:
                                if not task.done():
                                    task.cancel()
                                await asyncio.gather(task, return_exceptions=True)
                            if getattr(getattr(ctx.deps, 'cancel_token', None), 'cancelled', False):
                                raise ToolCancelled('运行已取消，丢弃晚到结果')
                            health_observation = getattr(text, "health_observation", None)
                            text = str(text)
                            from .evidence import outcome_for_text
                            status, code = outcome_for_text(text)
                            result = ToolResult(text, status, code)
                        except asyncio.TimeoutError:
                            result = ToolResult('工具超时：停止等待；底层同步操作可能仍在收尾，不自动重试', 'timeout', 'deadline_exceeded')
                        except (asyncio.CancelledError, ToolCancelled):
                            raise
                        except Exception as exc:
                            # SDK control flow must not become an ordinary tool error.
                            if isinstance(exc, getattr(ctx.deps, "control_flow_errors", ())):
                                raise
                            log.warning('tool failed name=%s error_type=%s', name, type(exc).__name__)
                            result = ToolResult(f'工具执行失败：{type(exc).__name__}', 'error', 'connector_error')
            from .context_data import external_text
            result = ToolResult(external_text(result.text), result.status, result.error_code)
            return result
        finally:
            if rec:
                rec.budget(ctx.deps)
                # A timeout closes the wait span, not a still-running sync execution.
                import sys
                exc=sys.exc_info()[1]
                status=result.status if result else ('cancelled' if type(exc).__name__ in ('RunCancelled','CancelledError','ToolCancelled') else 'failed')
                rec.end(span,status)
            record = {'tool': name, 'input': arguments, 'call_id': call_id,
                      'scope': {'org_id': scope.org_id, 'system_id': scope.system_id, 'run_id': scope.run_id},
                      'status': result.status if result else 'interrupted',
                      'error_code': result.error_code if result else 'control_flow',
                      'output': result.text[:1500] if result else '',
                      'observed_at': datetime.now(timezone.utc).isoformat(),
                      'duration_ms': round((time.monotonic() - began) * 1000)}
            if health_observation is not None and result and result.status == 'success':
                record['health_observation'] = health_observation
            records = getattr(ctx.deps, 'gateway_records', None)
            if records is not None:
                records.append(record)
            sink = getattr(ctx.deps, 'tool_audit_sink', None)
            if sink:
                # Admission/audit is mandatory for the platform path: failure propagates, no silent success.
                try:
                    sink(record)
                except Exception as exc:
                    if result is None:
                        log.exception("Audit failed during interruption")
                    else:
                        raise ToolAuditError("工具审计写入失败，不自动重跑") from exc


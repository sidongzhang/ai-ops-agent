"""Adapt the existing diagnosis engine without changing its prompt/tool/fallback logic."""
import logging
from datetime import datetime, timezone
from importlib.metadata import version
from uuid import uuid4

from .contracts import (
    HISTORY_SCHEMA_VERSION, RuntimeCapabilities, RuntimeCheckpoint,
    RuntimeCompatibilityError, RuntimeBudgetExceeded, RuntimeEvent, RuntimeRequest, RuntimeResult,
)
from .pydantic_history import load_messages, load_usage, usage_context, prepare_history

log = logging.getLogger(__name__)


class PydanticRuntime:
    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(engine="pydantic", engine_version=version("pydantic-ai-slim"))

    def create_cancellation(self):
        from pydantic_ai import CancellationToken

        return CancellationToken()

    def validate_checkpoint(self, checkpoint: RuntimeCheckpoint) -> None:
        if checkpoint.engine != "pydantic" or checkpoint.history_schema_version != HISTORY_SCHEMA_VERSION:
            raise RuntimeCompatibilityError("历史引擎或格式版本不兼容，不能用 Pydantic 恢复")
        current = self.capabilities().engine_version
        if checkpoint.engine_version and checkpoint.engine_version != current:
            raise RuntimeCompatibilityError(f"历史依赖 {checkpoint.engine_version}，当前为 {current}，尚未验证跨版本恢复")
        try:
            messages = load_messages(checkpoint.messages_json)
        except (ValueError, TypeError) as exc:
            raise RuntimeCompatibilityError("消息历史损坏，不能恢复") from exc
        if not messages:
            raise RuntimeCompatibilityError("没有可恢复的消息历史")

    def validate_resume_checkpoint(self, checkpoint):
        self.validate_checkpoint(checkpoint)
        load_usage(checkpoint.usage_json)

    def prepare_resume(self, checkpoint: RuntimeCheckpoint, *, force_refresh: bool, ttl_for_tool):
        self.validate_resume_checkpoint(checkpoint)
        return prepare_history(checkpoint.messages_json, force_refresh=force_refresh, ttl_for_tool=ttl_for_tool)

    def execute(self, request: RuntimeRequest) -> RuntimeResult:
        # Import lazily; existing monkeypatches and legacy direct entrypoints remain valid.
        from app.agent.diagnostics import runner

        options = dict(request.options)
        if request.checkpoint is not None:
            self.validate_resume_checkpoint(request.checkpoint)
            options["message_history"] = load_messages(request.checkpoint.messages_json)
            options["prior_usage"] = load_usage(request.checkpoint.usage_json)
            options["prior_usage_context"] = usage_context(request.checkpoint.usage_json)
        else:
            options["message_history"] = load_messages(options.get("message_history"))
            raw_usage = options.get("prior_usage")
            options["prior_usage"] = load_usage(raw_usage)
            if isinstance(raw_usage, str):
                options["prior_usage_context"] = usage_context(raw_usage)

        from ..harness.gateway import RunScope

        sequence = 0
        run_id = request.run_id or uuid4().hex
        options["runtime_scope"] = RunScope(request.org_id, request.system_id, run_id)

        def emit(kind, payload=None):
            nonlocal sequence
            if request.on_event is None:
                return
            sequence += 1
            event = RuntimeEvent(run_id, sequence, kind, datetime.now(timezone.utc).isoformat(), payload or {})
            try:
                request.on_event(event)
            except Exception:
                log.exception("[runtime] 事件观察失败 run=%s sequence=%s", run_id, sequence)

        # Installing an observer changes Pydantic to its streaming execution path.
        # Do not install one unless requested; preserve the current no-observer path.
        progress = options.get("on_progress")
        if request.on_event is not None:
            def on_progress(payload):
                kind = {"tool_start": "tool_started", "tool_end": "tool_finished"}.get(payload.get("kind"), payload.get("kind") or "runtime_progress")
                emit(kind, dict(payload))
                if progress is not None:
                    progress(payload)
            options["on_progress"] = on_progress

        emit("run_started", {"engine": "pydantic", "org_id": request.org_id, "system_id": request.system_id})
        try:
            result = runner.diagnose_with_details(
                request.descriptor, request.question,
                org_id=request.org_id, system_id=request.system_id, **options,
            )
        except BaseException as exc:
            from pydantic_ai.exceptions import UsageLimitExceeded
            from ..harness.execution import ExecutionUnsettled
            if isinstance(exc, ExecutionUnsettled):
                emit('runtime_unsettled', {'status':'stop_unconfirmed'})
                raise
            if isinstance(exc, (UsageLimitExceeded, RuntimeBudgetExceeded)):
                emit("runtime_settled", {"status": "budget_exceeded", "error_type": "RuntimeBudgetExceeded"})
                raise RuntimeBudgetExceeded("诊断用量达到限制，已停止；不自动降级重跑",
                    partial_result=getattr(exc,'partial_result',None)) from exc
            emit("runtime_settled", {"status": "failed", "error_type": type(exc).__name__})
            raise
        capabilities = self.capabilities()
        result.engine = capabilities.engine
        result.engine_version = capabilities.engine_version
        result.history_schema_version = capabilities.history_schema_version
        emit("runtime_settled", {"status": "cancelled" if result.cancelled else "success"})
        return result

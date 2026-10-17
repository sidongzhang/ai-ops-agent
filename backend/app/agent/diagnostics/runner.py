"""Diagnosis entrypoint."""
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Callable

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.usage import RunUsage
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.exceptions import RunCancelled, UsageLimitExceeded
from pydantic_ai.messages import (
    ToolCallPart,
    ToolReturnPart,
)

from app.agent.lazy_agent import LazyAgent
from app.core.config import settings
from app.agent.runtime.contracts import RuntimeResult as DiagnosisRun, RuntimeBudgetExceeded
from app.agent.harness.budget import RequestBudget, ToolBudget
from app.agent.harness.context_data import CONTEXT_RULES
from .request_control import ModelRequestGuard
from app.agent.harness.evidence import outcome_for_result
from app.agent.harness.execution import ExecutionTracker, ExecutionUnsettled, observe_remote_command
from .knowledge.store import append_runbook_entry
from .history import compact_messages
from .fallback import FallbackMode, decide_fallback
from .models import pick_model
from .tool_events import iter_tool_events, safe_emit
from .tools import AgentDeps, register_tools
from .tracing import get_langfuse

log = logging.getLogger(__name__)

# 延迟构造：import 本模块不应要求 LLM 凭证（CI/离线评测会直接 import）。
# 两个 agent 都在调用处显式传 model=，所以这里不绑定默认模型 —— 构造时也不会建立
# OpenAI 客户端，无凭证环境下 run 仍可用（评测用 TestModel 替换）。
diagnose_agent = LazyAgent(
    lambda: register_tools(Agent(deps_type=AgentDeps)),
    name="diagnose",
)
from ..harness.chat_profile import CHAT_RULES
chat_agent=LazyAgent(lambda:Agent(deps_type=AgentDeps,system_prompt=CHAT_RULES),name='chat')
text_fallback_agent = LazyAgent(
    lambda: Agent(
        output_type=str,
        instructions=(
            "你是智能运维诊断助手。工具调用链路失败时，请基于用户问题、系统描述、"
            "已知证据和错误信息给出可执行的中文诊断建议。不要输出 JSON。\n\n"
            + CONTEXT_RULES
        ),
    ),
    name="text_fallback",
)


def _build_prompt(question: str, images: list[tuple[str, bytes]] | None = None):
    """构造原生多模态输入，图片与文本一起交给所选模型。"""
    if not images:
        return question
    from pydantic_ai import BinaryContent

    return [
        *(BinaryContent(data=data, media_type=media) for media, data in images),
        question,
    ]


def diagnose(
    descriptor: dict,
    question: str,
    org_id: int = 0,
    system_id: int = 0,
    skill_steps: str | None = None,
) -> str:
    return diagnose_with_details(
        descriptor,
        question,
        org_id=org_id,
        system_id=system_id,
        skill_steps=skill_steps,
    ).answer


def diagnose_with_details(
    descriptor: dict,
    question: str,
    org_id: int = 0,
    system_id: int = 0,
    skill_steps: str | None = None,
    knowledge_context: str = "",
    trace_question: str | None = None,
    remote_command=None,
    business_data_query=None,
    business_dataset_query=None,
    archive_list=None,
    archive_read=None,
    archive_fetch=None,
    run_code=None,
    load_runbook=None,
    data_catalog: str = "",
    runbook_catalog: str = "",
    topology_catalog: str = "",
    topology_query=None,
    changes_catalog: str = "",
    changes_query=None,
    failure_catalog: str = "",
    failure_query=None,
    intent_text: str = "",
    conversation_context: str = "",
    mcp_catalog: str = "",
    images: list[tuple[str, bytes]] | None = None,
    model_mode: str = "auto",
    model_name: str = "",
    on_progress: Callable[[dict], None] | None = None,
    cancel_token=None,
    on_phase: Callable[[str], None] | None = None,
    message_history=None,
    prior_usage=None,
    prior_usage_context=None,
    prompt_override: str | None = None,
    clock_hint: str = "",
    tool_audit_sink=None,
    runtime_scope=None,
    interaction_kind="diagnosis",
    chat_context=None,
) -> DiagnosisRun:
    model = pick_model(intent_text or question, model_mode=model_mode, model_name=model_name)
    model_name = model.model_name if hasattr(model, "model_name") else str(model)
    langfuse = get_langfuse()
    trace = generation = None

    if langfuse:
        trace = langfuse.trace(
            name="diagnose",
            input=trace_question if trace_question is not None else question,
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

    from ..harness.chat_profile import CHAT_RULES,chat_prompt
    chat_only=interaction_kind=='chat'
    active_agent=chat_agent if chat_only else diagnose_agent
    started_at = time.monotonic()
    try:
        from ..harness.gateway import RunScope, ToolAuditError
        tracker = ExecutionTracker()
        run_usage = prior_usage if prior_usage is not None else RunUsage()
        run_limits = _usage_limits()
        baseline_context = prior_usage_context or {}
        tool_budget = ToolBudget(run_limits.tool_calls_limit, run_usage.tool_calls,
            reported_tool_calls_offset=baseline_context.get('reported_tool_calls_offset', 0))
        request_budget = RequestBudget(run_limits.request_limit, run_usage.requests,
            reported_requests_offset=baseline_context.get('reported_requests_offset', 0), tool_budget=tool_budget)
        deps = AgentDeps(
            descriptor=descriptor,
            scope=runtime_scope or RunScope(org_id, system_id),
            tool_audit_sink=tool_audit_sink,
            control_flow_errors=(RunCancelled, UsageLimitExceeded, ToolAuditError, ExecutionUnsettled, RuntimeBudgetExceeded),
            execution_tracker=tracker,
            request_budget=request_budget,
            tool_budget=tool_budget,
            usage_gaps=list(baseline_context.get('gaps', [])),
            question=question,
            skill_steps=skill_steps,
            knowledge_context=knowledge_context,
            conversation_context=conversation_context,
            remote_command=observe_remote_command(remote_command, tracker),
            business_data_query=business_data_query,
            business_dataset_query=business_dataset_query,
            archive_list=archive_list,
            archive_read=archive_read,
            archive_fetch=archive_fetch,
            run_code=run_code,
            load_runbook=load_runbook,
            data_catalog=data_catalog,
            runbook_catalog=runbook_catalog,
            topology_catalog=topology_catalog,
            topology_query=topology_query,
            changes_catalog=changes_catalog,
            changes_query=changes_query,
            failure_catalog=failure_catalog,
            failure_query=failure_query,
            mcp_catalog=mcp_catalog,
            intent_text=intent_text or question,
            progress_sink=on_progress,
            image_count=len(images or []),
            clock_hint=clock_hint,
            cancel_token=cancel_token,
        )
        if chat_only:deps.allowed_tools=frozenset()
        prompt = prompt_override or (chat_prompt(question,chat_context) if chat_only else _build_prompt(question, images))
        if on_phase is not None:
            on_phase("model_request")
        try:
            result = active_agent.run_sync(
                prompt,
                deps=deps,
                model=model,
                retries=3,
                usage_limits=run_limits,
                capabilities=[ProcessHistory(compact_messages), ModelRequestGuard(request_budget)],
                cancellation_token=cancel_token,
                message_history=message_history,
                usage=run_usage,
                **_stream_kwargs(on_progress, on_phase),
            )
        except RunCancelled as exc:
            # 框架原生取消：**必须最先捕获**，否则会落进下面的 except Exception 触发备用模型/降级。
            return _cancelled_run(exc, model_name, started_at, deps)
        except ExecutionUnsettled:
            raise
        except (UsageLimitExceeded, ToolAuditError, RuntimeBudgetExceeded):
            _ensure_executor_settled(deps, model_name, started_at)
            raise
        except Exception as exc:
            _ensure_executor_settled(deps, model_name, started_at)
            safe_emit(on_progress, {'kind': 'answer_attempt'})
            deps.usage_gaps.append('model_failed_attempt')
            decision = decide_fallback(model_mode=model_mode, has_images=bool(images))
            if decision.mode is FallbackMode.NONE:
                # 带图请求绝不降级到纯文本：丢弃图片比失败更糟
                raise RuntimeError("图片诊断执行失败，请确认所选模型支持图片或稍后重试；未丢弃图片改为纯文本回答。") from exc
            fallback = decision.model
            if decision.mode is FallbackMode.TEXT:
                result = _run_text_fallback(
                    model,
                    descriptor=descriptor,
                    question=question,
                    skill_steps=skill_steps or "",
                    knowledge_context=knowledge_context,
                    error=exc,
                    cancel_token=cancel_token, usage=run_usage, usage_limits=run_limits, request_budget=request_budget, on_progress=on_progress, on_phase=on_phase,
                    chat_deps=deps if chat_only else None, chat_context=chat_context,
                )
                model_name = _model_name(model)
            else:
                fallback_name = _model_name(fallback)
                log.warning(
                    "[diagnose] local model failed, fallback to API model=%s reason=%s error=%s",
                    fallback_name,
                    decision.reason,
                    exc,
                )
                model = fallback
                model_name = fallback_name
                try:
                    result = active_agent.run_sync(
                        prompt,
                        deps=deps,
                        model=model,
                        retries=3,
                        usage_limits=run_limits,
                        capabilities=[ModelRequestGuard(request_budget)],
                        cancellation_token=cancel_token,
                        message_history=message_history,
                        usage=run_usage,
                        **_stream_kwargs(on_progress, on_phase),
                    )
                except RunCancelled as cancel_exc:
                    return _cancelled_run(cancel_exc, model_name, started_at, deps)
                except ExecutionUnsettled:
                    raise
                except (UsageLimitExceeded, ToolAuditError, RuntimeBudgetExceeded):
                    _ensure_executor_settled(deps, model_name, started_at)
                    raise
                except Exception as fallback_exc:
                    _ensure_executor_settled(deps, model_name, started_at)
                    safe_emit(on_progress, {'kind': 'answer_attempt'})
                    deps.usage_gaps.append('fallback_failed_attempt')
                    log.warning(
                        "[diagnose] tool agent failed, fallback to text-only model=%s error=%s",
                        model_name,
                        fallback_exc,
                    )
                    result = _run_text_fallback(
                        model,
                        descriptor=descriptor,
                        question=question,
                        skill_steps=skill_steps or "",
                        knowledge_context=knowledge_context,
                        error=fallback_exc,
                        cancel_token=cancel_token, usage=run_usage, usage_limits=run_limits, request_budget=request_budget, on_progress=on_progress, on_phase=on_phase,
                    chat_deps=deps if chat_only else None, chat_context=chat_context,
                    )
        answer = _normalize_answer(result.output)
        usage = result.usage

        if generation:
            generation.end(
                output=answer,
                usage={
                    "input": usage.input_tokens or 0,
                    "output": usage.output_tokens or 0,
                },
            )
        if trace:
            trace.update(output=answer)

        elapsed = time.monotonic() - started_at
        log.info(
            f"[diagnose] system={system_id} model={model_name} "
            f"requests={getattr(usage, 'requests', 0)} "
            f"tokens={usage.total_tokens} "
            f"(in={usage.input_tokens} out={usage.output_tokens} "
            f"cache_read={usage.cache_read_tokens} cache_write={usage.cache_write_tokens}) "
            f"cost={getattr(usage, 'cost', None)} "
            f"tool_calls={getattr(usage, 'tool_calls', 0)} elapsed={elapsed:.2f}s"
        )

        _ensure_executor_settled(deps, model_name, started_at, DiagnosisRun(
            '', model_name, round((time.monotonic()-started_at)*1000),
            total_tokens=usage.total_tokens or 0, messages_json=_json_str(result.all_messages_json()),
            usage_json=_usage_json(usage, gap_reasons=deps.usage_gaps, request_budget=request_budget), conversation_id=str(getattr(result,'conversation_id','') or ''),
            run_id=str(getattr(result,'run_id','') or '')))
        elapsed = time.monotonic()-started_at
        # 自动把本次诊断摘要写入知识库 runbook（默认关闭，见 settings.diagnosis_auto_runbook）。
        # 开启后模型算错的数字会被当作"权威依据"污染后续诊断。
        # 修复：runbook 必须写到与检索一致的数字 system_id（旧版用 descriptor id
        # "org{n}-{key}" 建目录，检索却查 docs/<数字id>，经验回写从未被命中过）。
        if settings.diagnosis_auto_runbook and not chat_only:
            _append_to_runbook(system_id, descriptor, question, answer)

        # 合并子代理冒泡的工具调用（nested_tool_calls 由 investigate 写入 deps），
        # 保证审计链/评分/轨迹都能看到子代理内部的取证步骤。
        nested = [
            {"tool": c.get("tool", "unknown"), "input": c.get("input") or {},
             "status": c.get("status", "success"), "duration_ms": c.get("duration_ms", 0),
             "output": c.get("output", ""), "call_id": c.get("call_id", "")}
            for c in (deps.nested_tool_calls or [])
            if c.get("tool") and c.get("status") != "started"
        ]
        return DiagnosisRun(
            answer=answer,
            model=model_name,
            duration_ms=round(elapsed * 1000),
            total_tokens=usage.total_tokens or 0,
            tool_calls=_apply_gateway_records(_extract_tool_calls(result.all_messages()) + nested, deps.gateway_records),
            messages_json=_json_str(result.all_messages_json()),
            conversation_id=str(getattr(result, "conversation_id", "") or ""),
            run_id=str(getattr(result, "run_id", "") or ""),
            usage_json=_usage_json(usage, gap_reasons=deps.usage_gaps, request_budget=request_budget),
        )
    except RunCancelled as exc:
        return _cancelled_run(exc, model_name, started_at, deps)
    except (UsageLimitExceeded, ToolAuditError, RuntimeBudgetExceeded) as exc:
        _ensure_executor_settled(deps, model_name, started_at)
        exc.partial_result = DiagnosisRun('', model_name, round((time.monotonic()-started_at)*1000),
            total_tokens=run_usage.total_tokens or 0, run_id=getattr(deps.scope,'run_id',''), resume_safe=False,
            usage_json=_usage_json(run_usage,gap_reasons=deps.usage_gaps,request_budget=request_budget))
        raise
    except Exception as exc:
        if getattr(exc, 'partial_result', None) is None:
            exc.partial_result = DiagnosisRun('', model_name, round((time.monotonic()-started_at)*1000),
                total_tokens=run_usage.total_tokens or 0, run_id=getattr(deps.scope,'run_id',''), resume_safe=False,
                usage_json=_usage_json(run_usage,gap_reasons=deps.usage_gaps,request_budget=request_budget))
        if generation:
            generation.end(level="ERROR")
        raise
    finally:
        if langfuse:
            langfuse.flush()


def _stream_kwargs(on_progress: Callable[[dict], None] | None, on_phase=None) -> dict:
    """Attach an event-stream handler so callers can watch the tool chain live."""
    if on_progress is None and on_phase is None:
        return {}
    return {"event_stream_handler": _build_event_stream_handler(on_progress, on_phase)}


def _usage_limits() -> UsageLimits:
    """Bound one diagnosis so a looping agent cannot burn tokens indefinitely."""
    return UsageLimits(
        request_limit=settings.diagnosis_request_limit,
        tool_calls_limit=settings.diagnosis_tool_calls_limit,
        total_tokens_limit=settings.diagnosis_token_limit,
    )


def _build_event_stream_handler(on_progress, on_phase=None):
    """Translate pydantic-ai stream events into compact progress payloads.

    同时维护"执行相位"（模型请求 in-flight / 工具执行 / 空闲），用于取消时判定
    用量完整性：取消若落在 `model_request`，那次请求的用量可能拿不到。
    """
    async def handler(_ctx, events) -> None:
        async for payload in iter_tool_events(events, gateway_records=getattr(_ctx.deps, "gateway_records", None), include_text=True):
            if on_phase is not None:
                kind = payload.get("kind")
                if kind == "tool_start":
                    safe_phase(on_phase, "tool_exec")
                elif kind == "tool_end":
                    safe_phase(on_phase, "model_request")
            safe_emit(on_progress, payload)

    return handler


def safe_phase(on_phase, phase: str) -> None:
    try:
        on_phase(phase)
    except Exception:  # noqa: BLE001 - 相位上报绝不能影响诊断
        pass


def _json_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return str(value)


def _usage_json(usage, *, gap_reasons=(), request_budget=None) -> str:
    """框架用量 → JSON（cost 是价格表估算值，非账单）。缺失一律记 0，完整性由上游标记。"""
    def _num(name: str):
        value = getattr(usage, name, 0)
        return value if isinstance(value, (int, float)) else 0

    try:
        return json.dumps(
            {
                "input_tokens": _num("input_tokens"),
                "output_tokens": _num("output_tokens"),
                "cache_read_tokens": _num("cache_read_tokens"),
                "cache_write_tokens": _num("cache_write_tokens"),
                "total_tokens": _num("total_tokens"),
                "requests": request_budget.used if request_budget is not None else _num('requests'),
                'reported_requests':max(0, _num('requests') - getattr(request_budget, 'reported_requests_offset', 0)),
                "tool_calls": request_budget.tool_budget.used
                    if request_budget is not None and request_budget.tool_budget is not None else _num('tool_calls'),
                'reported_tool_calls': max(0, _num('tool_calls') - getattr(
                    getattr(request_budget, 'tool_budget', None), 'reported_tool_calls_offset', 0)),
                "cost": str(usage.cost) if getattr(usage,'cost',None) is not None else '',
                'usage_complete': usage is not None and not gap_reasons and all(
                    isinstance(getattr(usage,name,None),(int,float)) for name in ('input_tokens','output_tokens','requests'))
                    and (_num('requests')==0 or _num('total_tokens')>0)
                    and (request_budget is None or request_budget.used==_num('requests')),
                'cost_complete': getattr(usage,'cost',None) is not None and not gap_reasons
                    and (_num('requests')==0 or _num('total_tokens')>0)
                    and (request_budget is None or request_budget.used==_num('requests')),
                'gap_reason': ','.join(dict.fromkeys(gap_reasons)) if gap_reasons else (
                    'usage_missing' if usage is None else 'model_request_usage_unconfirmed'
                    if (request_budget is not None and request_budget.used!=_num('requests'))
                    or (_num('requests')>0 and _num('total_tokens')==0) else ''),
            },
            ensure_ascii=False,
        )
    except Exception as exc:  # noqa: BLE001 - 记账序列化失败必须留痕，不能静默成"无用量"
        log.warning("[diagnose] 用量序列化失败（将按不完整处理）: %s", exc)
        return ""


def _ensure_executor_settled(deps, model_name, started_at, partial=None):
    tracker = getattr(deps, 'execution_tracker', None)
    confirmed = tracker is None or tracker.wait_for_stop(settings.diagnosis_stop_wait_seconds)
    if partial is not None:
        partial.duration_ms = round((time.monotonic()-started_at)*1000)
    if not confirmed:
        partial = partial or DiagnosisRun('', model_name, round((time.monotonic()-started_at)*1000),
            run_id=getattr(deps.scope,'run_id',''), usage_json=json.dumps({
                'usage_complete':False,'cost_complete':False,'gap_reason':'execution_stop_unconfirmed'}))
        partial.resume_safe=False
        raise ExecutionUnsettled('工具执行停止未得到确认，可能仍在收尾；禁止继续，不自动重跑',partial_result=partial)


def _cancelled_run(exc, model_name: str, started_at: float, deps) -> DiagnosisRun:
    """把框架的 RunCancelled 转成可恢复结果（不触发任何降级/重试）。"""
    try:
        messages_json = exc.all_messages_json()
        if isinstance(messages_json, (bytes, bytearray)):
            messages_json = messages_json.decode("utf-8")
    except Exception:  # noqa: BLE001
        messages_json = ""
    usage = getattr(exc, "usage", None)
    usage_json = _usage_json(usage, gap_reasons=deps.usage_gaps, request_budget=deps.request_budget)
    nested = [
        {"tool": c.get("tool", "unknown"), "input": c.get("input") or {},
         "status": c.get("status", "success"), "duration_ms": c.get("duration_ms", 0),
         "output": c.get("output", ""), "call_id": c.get("call_id", "")}
        for c in (getattr(deps, "nested_tool_calls", None) or [])
        if c.get("tool") and c.get("status") != "started"
    ]
    result = DiagnosisRun(
        answer="",
        model=model_name,
        duration_ms=round((time.monotonic() - started_at) * 1000),
        total_tokens=getattr(usage, "total_tokens", 0) or 0,
        tool_calls=_apply_gateway_records(nested, getattr(deps, "gateway_records", [])),
        cancelled=True,
        messages_json=messages_json,
        conversation_id=str(getattr(exc, "conversation_id", "") or ""),
        run_id=str(getattr(exc, "run_id", "") or ""),
        usage_json=usage_json,
    )
    _ensure_executor_settled(deps, model_name, started_at, result)
    log.info('[diagnose] 取消与执行收尾已确认 model=%s', model_name)
    return result


_CONCLUSION_MARKER = re.compile(r"^[\s>*#\-]*(?:\*\*)?\s*结论\s*(?:\*\*)?\s*[:：]", re.MULTILINE)


def _normalize_answer(answer) -> str:
    """Drop any preamble the model emits before the mandatory 结论 field."""
    text = str(answer or "").strip()
    if not text:
        return text
    match = _CONCLUSION_MARKER.search(text)
    if match and match.start() > 0:
        text = text[match.start():].lstrip()
    return re.sub(r"^(?:[-*_]{3,}[ \t]*\n+)+", "", text).lstrip()


def _model_name(model) -> str:
    return model.model_name if hasattr(model, "model_name") else str(model)


def _run_text_fallback(
    model,
    *,
    descriptor: dict,
    question: str,
    skill_steps: str,
    knowledge_context: str,
    error: Exception,
    cancel_token=None,
    usage=None,
    usage_limits=None,
    request_budget=None,
    on_progress=None,
    on_phase=None,
    chat_deps=None, chat_context=None,
):
    if chat_deps is not None:
        from ..harness.chat_profile import chat_prompt
        return chat_agent.run_sync(chat_prompt(question,chat_context),deps=chat_deps,model=model,retries=2,
            cancellation_token=cancel_token,usage=usage,usage_limits=usage_limits,
            capabilities=[ModelRequestGuard(request_budget)] if request_budget is not None else [],
            **_stream_kwargs(on_progress,on_phase))
    from ..harness.context_data import CONTEXT_RULES, external_block, model_descriptor
    prompt = (
        f"用户问题：{question}\n\n"
        f"{CONTEXT_RULES}\n\n"
        f"已注册系统与服务：\n{external_block('系统注册信息', model_descriptor(descriptor))}\n\n"
        f"诊断模板：\n{external_block('本轮排查模板', skill_steps or '自由诊断')}\n\n"
        f"知识库上下文：\n{external_block('知识库检索', knowledge_context or '无')}\n\n"
        f"工具调用失败类型：{type(error).__name__}\n\n"
        "本次没有可用的取证工具，不能声称完成实时核查或执行修复。"
        "请给出：1. 当前能判断的结论；2. 还缺哪些证据；3. 下一步应该如何排查。"
    )
    return text_fallback_agent.run_sync(prompt, model=model, retries=2,
        cancellation_token=cancel_token, usage=usage, usage_limits=usage_limits,
        capabilities=[ModelRequestGuard(request_budget)] if request_budget is not None else [],
        **_stream_kwargs(on_progress, on_phase))


def _extract_tool_calls(messages: list) -> list[dict]:
    """Build a compact, serializable record from the agent's actual tool traffic."""
    calls: dict[str, dict] = {}
    ordered_ids: list[str] = []
    for message in messages:
        message_time = getattr(message, "timestamp", None)
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolCallPart):
                call_id = part.tool_call_id or f"{part.tool_name}:{len(ordered_ids)}"
                ordered_ids.append(call_id)
                try:
                    args = part.args_as_dict()
                except Exception:
                    args = str(part.args or "")[:1000]
                calls[call_id] = {
                    "tool": part.tool_name,
                    "call_id": call_id,
                    "input": args,
                    "status": "started",
                    "duration_ms": 0,
                    "_started_at": message_time,
                }
            elif isinstance(part, ToolReturnPart):
                call_id = part.tool_call_id
                item = calls.setdefault(
                    call_id,
                    {
                        "tool": part.tool_name,
                        "input": {},
                        "status": "started",
                        "duration_ms": 0,
                    },
                )
                item["status"], item["error_code"] = outcome_for_result(str(part.content), outcome=part.outcome)
                item["output"] = str(part.content)[:1500]
                started_at = item.pop("_started_at", None)
                ended_at = getattr(part, "timestamp", None) or message_time
                if ended_at:
                    item['observed_at'] = ended_at.isoformat()
                if started_at and ended_at:
                    item["duration_ms"] = max(0, round((ended_at - started_at).total_seconds() * 1000))

    result = []
    seen = set()
    for call_id in ordered_ids + list(calls):
        if call_id in seen:
            continue
        seen.add(call_id)
        item = calls[call_id]
        item.pop("_started_at", None)
        result.append(item)
    return result


def _append_to_runbook(system_id: int | str, descriptor: dict, question: str, answer: str) -> None:
    """把本次诊断的问题+摘要追加到该系统的 runbook，供后续 RAG 检索。"""
    try:
        # 数字 system_id 优先（与检索主键一致）；无数字 id 时才用 descriptor id
        doc_id = str(system_id) if system_id else descriptor.get("id", "default")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        # 取回答前 300 字作为摘要（避免写入过长）
        summary = answer[:300].replace("\n", " ").strip()
        entry = (
            f"## [{ts}] {question}\n\n"
            f"**摘要**: {summary}{'...' if len(answer) > 300 else ''}\n"
        )
        append_runbook_entry(doc_id, entry)
    except Exception as exc:
        log.debug(f"[rag] runbook 写入失败（不影响诊断结果）: {exc}")


def _apply_gateway_records(calls, records):
    by_id = {r["call_id"]: r for r in records}
    for call in calls:
        call_id = call.get("call_id", "")
        record = by_id.get(call_id) or by_id.get(call_id.removeprefix("sub-"))
        if record:
            for key in ("status", "error_code", "duration_ms", "observed_at", "health_observation", "scope"):
                if key not in record:
                    continue
                call[key] = record[key]
    # Experts have no legacy event handler; include their completed gateway traces.
    represented = {c.get("call_id", "").removeprefix("sub-") for c in calls}
    calls.extend(dict(r) for r in records if r["call_id"] not in represented)
    return calls

"""Diagnosis entrypoint."""
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.exceptions import RunCancelled
from pydantic_ai.messages import (
    ToolCallPart,
    ToolReturnPart,
)

from app.agent.llm import default_endpoint, endpoint_for_mode, make_chat_model
from app.agent.lazy_agent import LazyAgent
from app.core.config import settings
from .knowledge.store import append_runbook_entry
from .history import compact_messages
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
text_fallback_agent = LazyAgent(
    lambda: Agent(
        output_type=str,
        instructions=(
            "你是智能运维诊断助手。工具调用链路失败时，请基于用户问题、系统描述、"
            "已知证据和错误信息给出可执行的中文诊断建议。不要输出 JSON。"
        ),
    ),
    name="text_fallback",
)


def _build_prompt(question: str, images: list[tuple[str, bytes]] | None = None):
    """构造发给模型的输入：有截图时把图片作为**视觉证据**一并发出。"""
    if not images:
        return question
    from pydantic_ai import BinaryContent

    return [
        *(BinaryContent(data=data, media_type=media) for media, data in images),
        question,
    ]


@dataclass
class DiagnosisRun:
    answer: str
    model: str
    duration_ms: int
    total_tokens: int = 0
    tool_calls: list[dict] = field(default_factory=list)
    # 中断：框架原生取消（RunCancelled）时置 True，并带上可恢复的**完整消息历史**与用量
    cancelled: bool = False
    messages_json: str = ""
    conversation_id: str = ""
    run_id: str = ""
    usage_json: str = ""


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
    prompt_override: str | None = None,
) -> DiagnosisRun:
    model = pick_model(question, model_mode=model_mode, model_name=model_name)
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

    started_at = time.monotonic()
    try:
        deps = AgentDeps(
            descriptor=descriptor,
            question=question,
            skill_steps=skill_steps,
            knowledge_context=knowledge_context,
            conversation_context=conversation_context,
            remote_command=remote_command,
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
            cancel_token=cancel_token,
        )
        prompt = prompt_override or _build_prompt(question, images)
        if on_phase is not None:
            on_phase("model_request")
        try:
            result = diagnose_agent.run_sync(
                prompt,
                deps=deps,
                model=model,
                retries=3,
                usage_limits=_usage_limits(),
                capabilities=[ProcessHistory(compact_messages)],
                cancellation_token=cancel_token,
                message_history=message_history,
                usage=prior_usage,
                **_stream_kwargs(on_progress, on_phase),
            )
        except RunCancelled as exc:
            # 框架原生取消：**必须最先捕获**，否则会落进下面的 except Exception 触发备用模型/降级。
            return _cancelled_run(exc, model_name, started_at, deps)
        except Exception as exc:
            fallback = _fallback_api_model(model_mode)
            if not fallback:
                result = _run_text_fallback(
                    model,
                    descriptor=descriptor,
                    question=question,
                    skill_steps=skill_steps or "",
                    knowledge_context=knowledge_context,
                    error=exc,
                )
                model_name = _model_name(model)
            else:
                fallback_name = _model_name(fallback)
                log.warning(
                    "[diagnose] local model failed, fallback to API model=%s error=%s",
                    fallback_name,
                    exc,
                )
                model = fallback
                model_name = fallback_name
                try:
                    result = diagnose_agent.run_sync(
                        prompt,
                        deps=deps,
                        model=model,
                        retries=3,
                        usage_limits=_usage_limits(),
                        cancellation_token=cancel_token,
                        message_history=message_history,
                        usage=prior_usage,
                        **_stream_kwargs(on_progress, on_phase),
                    )
                except RunCancelled as cancel_exc:
                    return _cancelled_run(cancel_exc, model_name, started_at, deps)
                except Exception as fallback_exc:
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

        # 自动把本次诊断摘要写入知识库 runbook（默认关闭，见 settings.diagnosis_auto_runbook）。
        # 开启后模型算错的数字会被当作"权威依据"污染后续诊断。
        # 修复：runbook 必须写到与检索一致的数字 system_id（旧版用 descriptor id
        # "org{n}-{key}" 建目录，检索却查 docs/<数字id>，经验回写从未被命中过）。
        if settings.diagnosis_auto_runbook:
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
            tool_calls=_extract_tool_calls(result.all_messages()) + nested,
            messages_json=_json_str(result.all_messages_json()),
            conversation_id=str(getattr(result, "conversation_id", "") or ""),
            run_id=str(getattr(result, "run_id", "") or ""),
            usage_json=_usage_json(usage),
        )
    except Exception:
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
        async for payload in iter_tool_events(events):
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


def _usage_json(usage) -> str:
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
                "requests": _num("requests"),
                "tool_calls": _num("tool_calls"),
                "cost": str(getattr(usage, "cost", "") or ""),
            },
            ensure_ascii=False,
        )
    except Exception as exc:  # noqa: BLE001 - 记账序列化失败必须留痕，不能静默成"无用量"
        log.warning("[diagnose] 用量序列化失败（将按不完整处理）: %s", exc)
        return ""


def _cancelled_run(exc, model_name: str, started_at: float, deps) -> DiagnosisRun:
    """把框架的 RunCancelled 转成可恢复结果（不触发任何降级/重试）。"""
    try:
        messages_json = exc.all_messages_json()
        if isinstance(messages_json, (bytes, bytearray)):
            messages_json = messages_json.decode("utf-8")
    except Exception:  # noqa: BLE001
        messages_json = ""
    usage = getattr(exc, "usage", None)
    usage_json = _usage_json(usage)
    nested = [
        {"tool": c.get("tool", "unknown"), "input": c.get("input") or {},
         "status": c.get("status", "success"), "duration_ms": c.get("duration_ms", 0),
         "output": c.get("output", ""), "call_id": c.get("call_id", "")}
        for c in (getattr(deps, "nested_tool_calls", None) or [])
        if c.get("tool") and c.get("status") != "started"
    ]
    log.info(
        "[diagnose] 已取消（原生 RunCancelled）model=%s tokens=%s",
        model_name, getattr(usage, "total_tokens", 0),
    )
    return DiagnosisRun(
        answer="",
        model=model_name,
        duration_ms=round((time.monotonic() - started_at) * 1000),
        total_tokens=getattr(usage, "total_tokens", 0) or 0,
        tool_calls=nested,
        cancelled=True,
        messages_json=messages_json,
        conversation_id=str(getattr(exc, "conversation_id", "") or ""),
        run_id=str(getattr(exc, "run_id", "") or ""),
        usage_json=usage_json,
    )


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


def _fallback_api_model(model_mode: str):
    if (model_mode or "auto").strip().lower() not in {"", "auto", "default"}:
        return None
    if default_endpoint().mode != "local":
        return None
    api_endpoint = endpoint_for_mode("api")
    if not api_endpoint.api_key:
        return None
    return make_chat_model(api_endpoint)


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
):
    services = descriptor.get("services", [])
    service_lines = "\n".join(
        f"- {item.get('name', '')}: {item.get('connector', '')} {item.get('config', {})}"
        for item in services[:30]
    )
    prompt = (
        f"用户问题：{question}\n\n"
        f"系统名称：{descriptor.get('name', '')}\n"
        f"已注册服务：\n{service_lines or '无'}\n\n"
        f"诊断模板：\n{skill_steps or '自由诊断'}\n\n"
        f"知识库上下文：\n{knowledge_context or '无'}\n\n"
        f"工具调用失败原因：{error}\n\n"
        "请给出：1. 当前能判断的结论；2. 还缺哪些证据；3. 下一步应该如何排查。"
    )
    return text_fallback_agent.run_sync(prompt, model=model, retries=2)


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
                item["status"] = "success" if part.outcome == "success" else str(part.outcome)
                item["output"] = str(part.content)[:1500]
                started_at = item.pop("_started_at", None)
                ended_at = getattr(part, "timestamp", None) or message_time
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

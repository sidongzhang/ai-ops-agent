"""Diagnosis entrypoint."""
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ToolCallPart,
    ToolReturnPart,
)

from app.agent.llm import default_endpoint, endpoint_for_mode, make_chat_model
from app.core.config import settings
from .knowledge.store import append_runbook_entry
from .models import default_model, pick_model
from .tools import AgentDeps, register_tools
from .tracing import get_langfuse

log = logging.getLogger(__name__)

diagnose_agent = register_tools(Agent(default_model(), deps_type=AgentDeps))
text_fallback_agent = Agent(
    default_model(),
    output_type=str,
    instructions=(
        "你是智能运维诊断助手。工具调用链路失败时，请基于用户问题、系统描述、"
        "已知证据和错误信息给出可执行的中文诊断建议。不要输出 JSON。"
    ),
)


@dataclass
class DiagnosisRun:
    answer: str
    model: str
    duration_ms: int
    total_tokens: int = 0
    tool_calls: list[dict] = field(default_factory=list)


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
    data_catalog: str = "",
    model_mode: str = "auto",
    model_name: str = "",
    on_progress: Callable[[dict], None] | None = None,
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
            remote_command=remote_command,
            business_data_query=business_data_query,
            business_dataset_query=business_dataset_query,
            data_catalog=data_catalog,
        )
        try:
            result = diagnose_agent.run_sync(
                question,
                deps=deps,
                model=model,
                retries=3,
                usage_limits=_usage_limits(),
                **_stream_kwargs(on_progress),
            )
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
                        question,
                        deps=deps,
                        model=model,
                        retries=3,
                        usage_limits=_usage_limits(),
                        **_stream_kwargs(on_progress),
                    )
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
                    "input": usage.request_tokens or 0,
                    "output": usage.response_tokens or 0,
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
            f"tool_calls={getattr(usage, 'tool_calls', 0)} elapsed={elapsed:.2f}s"
        )

        # 自动把本次诊断摘要写入知识库 runbook（默认关闭，见 settings.diagnosis_auto_runbook）。
        # 开启后模型算错的数字会被当作"权威依据"污染后续诊断。
        # 修复：runbook 必须写到与检索一致的数字 system_id（旧版用 descriptor id
        # "org{n}-{key}" 建目录，检索却查 docs/<数字id>，经验回写从未被命中过）。
        if settings.diagnosis_auto_runbook:
            _append_to_runbook(system_id, descriptor, question, answer)

        return DiagnosisRun(
            answer=answer,
            model=model_name,
            duration_ms=round(elapsed * 1000),
            total_tokens=usage.total_tokens or 0,
            tool_calls=_extract_tool_calls(result.all_messages()),
        )
    except Exception:
        if generation:
            generation.end(level="ERROR")
        raise
    finally:
        if langfuse:
            langfuse.flush()


def _stream_kwargs(on_progress: Callable[[dict], None] | None) -> dict:
    """Attach an event-stream handler so callers can watch the tool chain live."""
    if on_progress is None:
        return {}
    return {"event_stream_handler": _build_event_stream_handler(on_progress)}


def _usage_limits() -> UsageLimits:
    """Bound one diagnosis so a looping agent cannot burn tokens indefinitely."""
    return UsageLimits(
        request_limit=settings.diagnosis_request_limit,
        tool_calls_limit=settings.diagnosis_tool_calls_limit,
        total_tokens_limit=settings.diagnosis_token_limit,
    )


def _build_event_stream_handler(on_progress: Callable[[dict], None]):
    """Translate pydantic-ai stream events into compact progress payloads."""
    started: dict[str, float] = {}

    async def handler(_ctx, events) -> None:
        async for event in events:
            if isinstance(event, FunctionToolCallEvent):
                part = event.part
                call_id = part.tool_call_id or f"{part.tool_name}:{len(started)}"
                started[call_id] = time.monotonic()
                try:
                    args = part.args_as_dict()
                except Exception:
                    args = str(part.args or "")[:1000]
                _safe_progress(
                    on_progress,
                    {
                        "kind": "tool_start",
                        "call_id": call_id,
                        "tool": part.tool_name,
                        "input": args,
                    },
                )
            elif isinstance(event, FunctionToolResultEvent):
                part = event.part
                call_id = getattr(part, "tool_call_id", "") or ""
                began = started.pop(call_id, None)
                outcome = getattr(part, "outcome", "success")
                output = getattr(part, "content", None) or event.content or ""
                _safe_progress(
                    on_progress,
                    {
                        "kind": "tool_end",
                        "call_id": call_id,
                        "tool": getattr(part, "tool_name", "") or "",
                        "status": "success" if outcome == "success" else str(outcome),
                        "duration_ms": round((time.monotonic() - began) * 1000) if began else 0,
                        "output": str(output)[:1500],
                    },
                )

    return handler


def _safe_progress(on_progress: Callable[[dict], None], payload: dict) -> None:
    try:
        on_progress(payload)
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[diagnose] 进度回调失败（不影响诊断）: {exc}")


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

"""Diagnosis entrypoint."""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic_ai import Agent
from pydantic_ai.messages import ToolCallPart, ToolReturnPart

from .knowledge.store import append_runbook_entry
from .models import default_model, pick_model
from .tools import AgentDeps, register_tools
from .tracing import get_langfuse

log = logging.getLogger(__name__)

diagnose_agent = register_tools(Agent(default_model(), deps_type=AgentDeps))


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
) -> DiagnosisRun:
    model = pick_model(question)
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
        result = diagnose_agent.run_sync(
            question,
            deps=AgentDeps(
                descriptor=descriptor,
                question=question,
                skill_steps=skill_steps,
                knowledge_context=knowledge_context,
                remote_command=remote_command,
                business_data_query=business_data_query,
            ),
            model=model,
        )
        answer = result.output
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
            f"tokens={usage.total_tokens} elapsed={elapsed:.2f}s"
        )

        # 自动将本次诊断摘要写入知识库 runbook（供后续 RAG 参考）
        _append_to_runbook(descriptor, question, answer)

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


def _append_to_runbook(descriptor: dict, question: str, answer: str) -> None:
    """把本次诊断的问题+摘要追加到该系统的 runbook，供后续 RAG 检索。"""
    try:
        system_id = descriptor.get("id", "default")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        # 取回答前 300 字作为摘要（避免写入过长）
        summary = answer[:300].replace("\n", " ").strip()
        entry = (
            f"## [{ts}] {question}\n\n"
            f"**摘要**: {summary}{'...' if len(answer) > 300 else ''}\n"
        )
        append_runbook_entry(system_id, entry)
    except Exception as exc:
        log.debug(f"[rag] runbook 写入失败（不影响诊断结果）: {exc}")

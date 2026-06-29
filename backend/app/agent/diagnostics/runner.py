"""Diagnosis entrypoint."""
import logging
import time
from datetime import datetime, timezone

from pydantic_ai import Agent

from .knowledge.store import append_runbook_entry
from .models import default_model, pick_model
from .tools import AgentDeps, register_tools
from .tracing import get_langfuse

log = logging.getLogger(__name__)

diagnose_agent = register_tools(Agent(default_model(), deps_type=AgentDeps))


def diagnose(
    descriptor: dict,
    question: str,
    org_id: int = 0,
    system_id: int = 0,
) -> str:
    model = pick_model(question)
    model_name = model.model_name if hasattr(model, "model_name") else str(model)
    langfuse = get_langfuse()
    trace = generation = None

    if langfuse:
        trace = langfuse.trace(
            name="diagnose",
            input=question,
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
            deps=AgentDeps(descriptor=descriptor, question=question),
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

        return answer
    except Exception:
        if generation:
            generation.end(level="ERROR")
        raise
    finally:
        if langfuse:
            langfuse.flush()


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

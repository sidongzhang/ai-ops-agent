"""Build structured evidence chains from agent tool calls and analysis payloads."""
from __future__ import annotations

from typing import Any

from app.agent.diagnostics.knowledge.store import read_doc, search_with_memories

TOOL_LABELS = {
    "check_service": "健康检查",
    "list_services": "服务列表",
    "investigate": "委派取证（子代理）",
    "read_logs": "日志分析",
    "search_logs": "日志检索",
    "query_prometheus": "Prometheus 指标",
    "run_redis_command": "Redis 只读命令",
    "run_kafka_command": "Kafka 只读命令",
    "run_readonly_query": "只读 SQL",
    "search_knowledge_base": "运维知识库",
    "query_business_data": "只读业务数据",
    "fetch_logs": "拉取日志",
    "health_check": "健康检查",
}

STATUS_LABELS = {
    "success": "成功",
    "started": "执行中",
    "error": "失败",
}


def _summarize_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _format_input(payload: Any) -> str:
    if isinstance(payload, dict):
        parts = []
        for key, value in payload.items():
            if value in (None, "", [], {}):
                continue
            parts.append(f"{key}={_summarize_text(value, 80)}")
        return "，".join(parts) if parts else "无参数"
    return _summarize_text(payload, 120)


def summarize_text(value: Any, limit: int = 240) -> str:
    """Public wrapper used by the incremental progress writer."""
    return _summarize_text(value, limit)


def build_evidence_from_tool_calls(tool_calls: list[dict]) -> list[dict]:
    items: list[dict] = []
    for index, call in enumerate(tool_calls, start=1):
        tool = str(call.get("tool") or "unknown")
        status = str(call.get("status") or "unknown")
        output = call.get("output", "")
        items.append(
            {
                "step": index,
                "type": tool,
                "label": TOOL_LABELS.get(tool, tool),
                "detail": _summarize_text(output),
                "status": status,
                "duration_ms": int(call.get("duration_ms") or 0),
                "input": call.get("input") or {},
                "output": str(output)[:2000],
            }
        )
    return items


def build_evidence_steps(tool_calls: list[dict]) -> list[str]:
    steps: list[str] = []
    for index, call in enumerate(tool_calls, start=1):
        tool = str(call.get("tool") or "unknown")
        label = TOOL_LABELS.get(tool, tool)
        status = STATUS_LABELS.get(str(call.get("status") or ""), str(call.get("status") or "未知"))
        detail = _summarize_text(call.get("output"), 120)
        suffix = f" · {detail}" if detail else ""
        steps.append(f"{index}. {label}（{status}）{suffix}")
    return steps


def build_evidence_from_data_analysis(evidence: dict | None) -> tuple[list[dict], list[str]]:
    payload = evidence or {}
    items: list[dict] = []
    steps: list[str] = []

    def add(step: int, label: str, detail: str, *, item_type: str = "data_analysis", output: str = "") -> None:
        items.append(
            {
                "step": step,
                "type": item_type,
                "label": label,
                "detail": detail,
                "status": "success",
                "duration_ms": 0,
                "input": {},
                "output": output[:2000],
            }
        )
        steps.append(f"{step}. {label} · {detail}")

    step = 1
    source = payload.get("data_source") or payload.get("table") or "只读数据源"
    add(step, "只读数据源", f"查询 {source}")
    step += 1

    if payload.get("analysis_type") == "stuck_tasks":
        threshold = payload.get("stuck_threshold_minutes")
        stuck_count = payload.get("stuck_count", 0)
        add(step, "卡住任务统计", f"超过 {threshold} 分钟未更新的任务 {stuck_count} 条")
        step += 1
        worker_health = payload.get("worker_health") or []
        if worker_health:
            summary = "、".join(
                f"{item.get('name', 'worker')}{'正常' if item.get('ok') else '异常'}"
                for item in worker_health
            )
            add(step, "Worker 健康检查", summary)
            step += 1
    else:
        total = payload.get("total")
        if total is not None:
            scope = "今天" if payload.get("today_only") else "当前条件"
            add(step, "记录统计", f"{scope}匹配 {total} 条")
            step += 1

    sample_rows = payload.get("sample_rows") or []
    if sample_rows:
        add(step, "样例数据", f"返回 {len(sample_rows)} 条，敏感字段已脱敏")
        step += 1

    count_sql = payload.get("count_sql")
    if count_sql:
        add(step, "执行 SQL", _summarize_text(count_sql, 160), item_type="run_readonly_query", output=str(count_sql))

    return items, steps


def build_knowledge_refs(
    system_id: str | int,
    tool_calls: list[dict],
    *,
    question: str = "",
    max_doc_chars: int = 6000,
) -> list[dict]:
    """Collect knowledge-base documents referenced during diagnosis."""
    refs: list[dict] = []
    seen: set[str] = set()

    def add_hits(hits: list[dict]) -> None:
        for hit in hits:
            name = hit.get("name") or ""
            if not name or name in seen:
                continue
            seen.add(name)
            content = read_doc(str(system_id), name) or hit.get("snippet", "")
            truncated = False
            if len(content) > max_doc_chars:
                content = content[:max_doc_chars].rstrip() + "\n\n…（后文已截断）"
                truncated = True
            refs.append(
                {
                    "name": name,
                    "snippet": hit.get("snippet", ""),
                    "score": hit.get("score"),
                    "content": content,
                    "truncated": truncated,
                }
            )

    for call in tool_calls:
        if call.get("tool") != "search_knowledge_base":
            continue
        payload = call.get("input") or {}
        query = payload.get("query") if isinstance(payload, dict) else ""
        if not query:
            continue
        add_hits(search_with_memories(query, str(system_id)))

    if question:
        add_hits(search_with_memories(question, str(system_id)))

    return refs

"""继续（resume）：把被中断的诊断从**原生历史**接着跑。

三条硬规则：
  1. **幂等**：`diagnosis_reports.resumed_from_report_id` 唯一约束。重复点击/并发点击
     只有一个能插入成功，其余直接返回已存在的那条（不产生第二次执行、不重复花钱）。
  2. **诚实**：不可恢复（无历史 / 历史损坏 / 源报告已成功）→ 明确报错，绝不制造
     "必然失败的继续"。
  3. **过期证据要重新取证**：历史里的工具结果按工具类别 TTL 标注年龄；`force_refresh`
     时把超期结果替换为"需要重新取证"标记（保留 tool_call/return 配对，不破坏历史合法性）。

记账：`run_kind="resume"`，baseline = 源报告的框架累计用量 → 增量 = after − before，
会话累计 = SUM(ledger.delta)，不会把框架累计值重复累加。
"""
import json
import logging
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.core.config import settings
from app.models.diagnostics import DiagnosisReport

log = logging.getLogger(__name__)

RESUME_KIND = "resume"
# 可继续的源状态：被中断 / 失败（成功没什么可继续的）
RESUMABLE_STATUSES = ("cancelled", "failed")

# 工具 → 证据时效类别（决定"多久算过期"）
_TOOL_CLASS = {
    "list_services": "health",
    "check_service": "health",
    "check_container_state": "health",
    "read_logs": "health",
    "search_logs": "health",
    "query_prometheus": "metrics",
    "run_kafka_command": "metrics",
    "run_redis_command": "metrics",
    "query_business_data": "business",
    "query_business_dataset": "business",
    "list_archive_files": "business",
    "read_archive_file": "business",
    "fetch_archive_file": "business",
    "get_service_topology": "static",
    "analyze_root_cause": "static",
    "recent_changes": "static",
    "search_knowledge_base": "static",
    "load_runbook": "static",
    "run_python_analysis": "business",
    "investigate": "metrics",
    "consult_specialists": "metrics",
    "mcp_list_tools": "static",
    "mcp_call": "metrics",
}


class ResumeNotPossible(RuntimeError):
    """源报告不可继续（无历史 / 历史损坏 / 已成功）。API 层转 409。"""

    def __init__(self, message: str, reason: str = "not_resumable") -> None:
        super().__init__(message)
        self.reason = reason


def _ttl_seconds(tool: str) -> int:
    table = settings.evidence_stale_ttl or {}
    return int(table.get(_TOOL_CLASS.get(tool, "metrics"), 300))


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _age_minutes(stamp) -> float | None:
    moment = _aware(stamp if isinstance(stamp, datetime) else None)
    if moment is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - moment).total_seconds() / 60.0)


def prepare_resume(
    session: Session,
    source: DiagnosisReport,
    *,
    force_refresh: bool = False,
) -> dict:
    """把源报告的可恢复历史整理成续跑载荷（含过期证据处理与提示文本）。

    返回 {"history_json", "history", "prior_usage", "baseline_json", "note", "stale"}。
    """
    from pydantic_ai.messages import ModelMessagesTypeAdapter, repair_messages
    from pydantic_ai.usage import RunUsage

    from app.models.run_state import DiagnosisRunState
    from app.services.diagnostics.run_state import resumable_of

    if source.status not in RESUMABLE_STATUSES:
        raise ResumeNotPossible(
            f"该诊断状态为 {source.status}，无需继续" if source.status == "success"
            else f"该诊断状态为 {source.status}，不可继续",
            reason="not_resumable",
        )
    if not resumable_of(source.id, session=session):
        raise ResumeNotPossible(
            "本次未取得可恢复历史（执行方失联或历史损坏），无法继续；请重新提问",
            reason="no_history",
        )

    state = session.get(DiagnosisRunState, source.id)
    history = ModelMessagesTypeAdapter.validate_json(state.messages_json)
    history = repair_messages(history)                 # 关掉悬空工具调用，保证历史合法
    history, stale = _annotate_stale(history, force_refresh=force_refresh)

    baseline_json = state.framework_cumulative_json or ""
    prior = RunUsage()
    if baseline_json:
        try:
            raw = json.loads(baseline_json)
            prior.input_tokens = int(raw.get("input_tokens") or 0)
            prior.output_tokens = int(raw.get("output_tokens") or 0)
            prior.cache_read_tokens = int(raw.get("cache_read_tokens") or 0)
            prior.cache_write_tokens = int(raw.get("cache_write_tokens") or 0)
            prior.requests = int(raw.get("requests") or 0)
            prior.tool_calls = int(raw.get("tool_calls") or 0)
        except Exception as exc:  # noqa: BLE001 - baseline 解析失败按 0 起算并留痕
            log.warning("[resume] baseline 解析失败 report=%s: %s", source.id, exc)

    note = _build_resume_note(source, stale)
    return {
        "history_json": ModelMessagesTypeAdapter.dump_json(history).decode("utf-8"),
        "history": history,
        "prior_usage": prior,
        "baseline_json": baseline_json,
        "note": note,
        "stale": stale,
    }


def _annotate_stale(history: list, *, force_refresh: bool) -> tuple[list, list[dict]]:
    """标注过期证据；force_refresh 时把过期结果**替换为需重新取证的标记**。

    只改 ToolReturnPart 的 content，保留 tool_call_id 配对 → 历史依然对 provider 合法。
    """
    from pydantic_ai.messages import ModelRequest, ToolReturnPart

    stale: list[dict] = []
    for message in history:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            age = _age_minutes(getattr(part, "timestamp", None))
            ttl = _ttl_seconds(part.tool_name)
            if age is None or age * 60 <= ttl:
                continue
            stale.append({"tool": part.tool_name, "age_minutes": round(age, 1), "ttl_seconds": ttl})
            if force_refresh:
                part.content = (
                    f"（该证据采集于约 {age:.0f} 分钟前，已超过 {ttl // 60} 分钟时效，"
                    "可能过期——需要时请重新调用工具取证）"
                )
    return history, stale


def _build_resume_note(source: DiagnosisReport, stale: list[dict]) -> str:
    lines = [
        "【继续上次被中断的诊断】",
        f"原始问题：{source.question or '（未记录）'}",
        "上次执行已完成的取证与结论都在对话历史里；请**接着**完成，不要从头重来。",
    ]
    if stale:
        lines.append("")
        lines.append("⚠️ 以下证据**可能已过期**（按类别时效判断），若对结论关键请重新调用工具取证：")
        for item in stale[:8]:
            lines.append(f"  - {item['tool']}（约 {item['age_minutes']:.0f} 分钟前）")
        lines.append("其余较新的证据可直接复用，避免重复调用。")
    lines.append("")
    lines.append("请给出**最终结论**（结论/影响/关键证据/建议）。")
    return "\n".join(lines)


def find_existing_resume(session: Session, source_report_id: int) -> DiagnosisReport | None:
    return session.exec(
        select(DiagnosisReport).where(DiagnosisReport.resumed_from_report_id == source_report_id)
    ).first()


def load_history_for_run(payload: dict):
    """从载荷取回反序列化好的历史（供 runner 使用）。"""
    if not payload:
        return None
    history = payload.get("history")
    if history:
        return history
    json_str = payload.get("history_json") or ""
    if not json_str:
        return None
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    return ModelMessagesTypeAdapter.validate_json(json_str)

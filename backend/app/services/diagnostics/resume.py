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

from app.models.diagnostics import DiagnosisReport

log = logging.getLogger(__name__)

RESUME_KIND = "resume"
# 可继续的源状态：被中断 / 失败（成功没什么可继续的）
RESUMABLE_STATUSES = ("cancelled", "failed")

# 工具 → 证据时效类别（决定"多久算过期"）
from app.agent.harness.freshness import ttl_seconds as _ttl_seconds


class ResumeNotPossible(RuntimeError):
    """源报告不可继续（无历史 / 历史损坏 / 已成功）。API 层转 409。"""

    def __init__(self, message: str, reason: str = "not_resumable") -> None:
        super().__init__(message)
        self.reason = reason


def prepare_resume(
    session: Session,
    source: DiagnosisReport,
    *,
    force_refresh: bool = False,
) -> dict:
    """把源报告的可恢复历史整理成续跑载荷（含过期证据处理与提示文本）。

    返回 {"history_json", "history", "prior_usage", "baseline_json", "note", "stale"}。
    """
    from app.agent.runtime.contracts import RuntimeCheckpoint, RuntimeCompatibilityError, UsageBaseline
    from app.agent.runtime.factory import get_runtime

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
    checkpoint = RuntimeCheckpoint(
        state.messages_json, state.framework_cumulative_json,
        state.engine, state.engine_version, state.history_schema_version,
    )
    try:
        history_json, stale = get_runtime(state.engine).prepare_resume(
            checkpoint, force_refresh=force_refresh, ttl_for_tool=_ttl_seconds,
        )
    except RuntimeCompatibilityError as exc:
        raise ResumeNotPossible(str(exc), reason="incompatible_runtime") from exc

    baseline_json = state.framework_cumulative_json or ""
    try:
        raw = json.loads(baseline_json or "{}")
        prior = UsageBaseline(**{key: int(raw.get(key) or 0) for key in UsageBaseline.__dataclass_fields__})
    except Exception as exc:
        log.warning("[resume] baseline 解析失败 report=%s: %s", source.id, exc)
        prior = UsageBaseline()
    return {
        "history_json": history_json,
        "prior_usage": prior,
        "baseline_json": baseline_json,
        "note": _build_resume_note(source, stale),
        "stale": stale,
        "engine": state.engine,
        "engine_version": state.engine_version,
        "history_schema_version": state.history_schema_version,
    }


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
    """Return opaque history; native message decoding belongs to the selected runtime."""
    return (payload or {}).get("history_json") or None

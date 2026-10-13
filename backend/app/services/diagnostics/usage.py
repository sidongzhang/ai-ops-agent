"""用量与费用记账：完整性判定、增量账本、会话预算（原子预留/结算）。

三条硬规则（来自设计文档 §2.5）：
  1. **有 usage ≠ 用量完整**：取消落在"模型请求在途"时，该请求的用量不可得 → 必须标
     `usage_complete=false` + gap reason。
  2. **未知用量不得按免费处理**：结算时按 `max(实测, 保守预留)` 计入会话预算。
  3. **只累加增量**：resume 时框架返回的是累计值，增量 = after − before；
     会话累计 = SUM(ledger.tokens_delta)，绝不重复累加。

费用一律是**估算**（框架按价格表推算），展示必须标 "≈"。
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.core.config import settings
from app.models.diagnostics import DiagnosisReport
from app.models.usage import DiagnosisUsageLedger

log = logging.getLogger(__name__)

GAP_IN_FLIGHT = "in_flight_request_aborted"
GAP_PROVIDER = "provider_no_usage"
GAP_PROCESS_KILLED = "process_killed"

_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "total_tokens",
    "requests",
    "tool_calls",
)


class BudgetExceeded(RuntimeError):
    """会话预算不足（滚动窗口内累计已用 + 本次预留超限）。"""

    def __init__(self, message: str, detail: dict) -> None:
        super().__init__(message)
        self.detail = detail


def parse_usage(usage_json: str) -> dict:
    try:
        raw = json.loads(usage_json or "{}")
    except Exception:  # noqa: BLE001
        return {}
    out: dict = {}
    for key in _USAGE_FIELDS:
        value = raw.get(key)
        out[key] = value if isinstance(value, (int, float)) else 0
    cost = raw.get("cost")
    try:
        out["cost"] = float(cost) if cost not in (None, "") else 0.0
    except (TypeError, ValueError):
        out["cost"] = 0.0
    return out


def assess_completeness(*, cancelled: bool, cancel_phase: str, usage: dict) -> tuple[bool, str]:
    """判定本次 run 的用量是否完整。返回 (complete, gap_reason)。"""
    if cancelled:
        if cancel_phase == "model_request":
            return False, GAP_IN_FLIGHT
        # 取消在工具间隙/启动前：已完成请求的用量应当是完整的；但若框架没给出任何用量
        # 而消息历史显示确实跑过（requests=0 且 total=0），视为 provider 未返回。
        if usage.get("requests", 0) == 0 and usage.get("total_tokens", 0) == 0 and cancel_phase != "idle":
            return False, GAP_PROVIDER
        return True, ""
    if usage.get("requests", 0) == 0 and usage.get("total_tokens", 0) == 0:
        return False, GAP_PROVIDER
    return True, ""


def _window_start(moment: datetime) -> datetime:
    seconds = max(60, settings.diagnosis_session_window_seconds)
    epoch = int(moment.timestamp())
    return datetime.fromtimestamp((epoch // seconds) * seconds, tz=timezone.utc)


def record_run_usage(
    *,
    report_id: int,
    org_id: int,
    system_id: int,
    conversation_id: str = "",
    run_kind: str = "initial",
    usage_json: str = "",
    cancelled: bool = False,
    cancel_phase: str = "",
    before_json: str | None = None,
    session: Session | None = None,
) -> dict:
    """写一行**增量**账本并回填报告用量字段。返回 {"tokens_delta","settle_tokens","cost_delta",...}。

    `before_json` 显式传入"上一次的框架累计"（baseline）：调用方必须在**覆盖 run_state 之前**
    取到它，否则 after==before，增量会被算成 0。
    """
    from app.services.diagnostics.session import session as _db_session

    def _work(session: Session) -> dict:
        from app.models.run_state import DiagnosisRunState

        report = session.get(DiagnosisReport, report_id)
        if report is None:
            return {"tokens_delta": 0, "cost_delta": 0.0, "usage_complete": True}

        after = parse_usage(usage_json)
        state = session.get(DiagnosisRunState, report_id)
        baseline = before_json if before_json is not None else (state.framework_cumulative_json if state else "")
        before = parse_usage(baseline)

        complete, gap = assess_completeness(
            cancelled=cancelled, cancel_phase=cancel_phase, usage=after
        )
        delta = {k: max(0, int(after.get(k, 0)) - int(before.get(k, 0))) for k in _USAGE_FIELDS}
        cost_delta = max(0.0, round(after.get("cost", 0.0) - before.get("cost", 0.0), 6))

        # 未知用量：按保守预估计入（不得当免费）
        estimated_portion = not complete
        settle_tokens = delta["total_tokens"]
        if estimated_portion:
            settle_tokens = max(settle_tokens, settings.diagnosis_budget_reserve_tokens)

        ledger = DiagnosisUsageLedger(
            org_id=org_id,
            system_id=system_id,
            report_id=report_id,
            conversation_id=conversation_id or (state.conversation_id if state else ""),
            run_kind=run_kind,
            tokens_input=delta["input_tokens"],
            tokens_output=delta["output_tokens"],
            tokens_cache_read=delta["cache_read_tokens"],
            tokens_cache_write=delta["cache_write_tokens"],
            tokens_delta=delta["total_tokens"],
            cost_usd_delta=cost_delta,
            usage_complete=complete,
            estimated_portion=estimated_portion,
        )
        session.add(ledger)

        report.total_tokens = (report.total_tokens or 0) + delta["total_tokens"]
        report.usage_complete = complete if report.usage_complete is None else (report.usage_complete and complete)
        if gap:
            report.usage_gap_reason = gap
        report.cost_usd_estimated = float(report.cost_usd_estimated or 0) + cost_delta
        if not report.price_table_version:
            report.price_table_version = "genai-prices@2.55.0"
        session.add(report)
        session.commit()
        return {
            "tokens_delta": delta["total_tokens"],
            "settle_tokens": settle_tokens,
            "cost_delta": cost_delta,
            "usage_complete": complete,
            "gap_reason": gap,
            "estimated_portion": estimated_portion,
        }

    if session is not None:
        return _work(session)
    with _db_session() as own:
        return _work(own)


def session_usage(org_id: int, system_id: int, *, conversation_id: str = "", session: Session | None = None) -> dict:
    """会话累计 = SUM(ledger 增量)。绝不把框架累计值再累加一遍。"""
    from app.services.diagnostics.session import session as _db_session

    def _work(session: Session) -> dict:
        query = select(DiagnosisUsageLedger).where(
            DiagnosisUsageLedger.org_id == org_id, DiagnosisUsageLedger.system_id == system_id
        )
        if conversation_id:
            query = query.where(DiagnosisUsageLedger.conversation_id == conversation_id)
        rows = session.exec(query).all()
        return {
            "tokens": sum(int(r.tokens_delta or 0) for r in rows),
            "cost_usd": round(sum(float(r.cost_usd_delta or 0) for r in rows), 6),
            "runs": len(rows),
            "incomplete_runs": sum(1 for r in rows if not r.usage_complete),
        }

    if session is not None:
        return _work(session)
    with _db_session() as own:
        return _work(own)


# ── 会话预算：原子预留 / 结算（覆盖并发请求）────────────────────────────
def _budget_sql_upsert(reserved_tokens: int, reserved_cost: float, window_start: datetime) -> str:
    return """
        INSERT INTO diagnosis_budget_ledger
            (org_id, user_id, system_id, window_started_at, reserved_tokens, settled_tokens,
             reserved_cost, settled_cost, updated_at)
        VALUES (:org, :uid, :sid, :win, :rt, 0, :rc, 0, :now)
        ON CONFLICT (org_id, user_id, system_id, window_started_at)
        DO UPDATE SET reserved_tokens = diagnosis_budget_ledger.reserved_tokens + :rt,
                      reserved_cost   = diagnosis_budget_ledger.reserved_cost + :rc,
                      updated_at      = :now
    """


def reserve_budget(
    *, org_id: int, user_id: int, system_id: int, moment: datetime | None = None
) -> dict:
    """提交前原子预留。返回 {"allowed": bool, "used_tokens": n, "limit": n}。

    并发安全：先原子加预留，再在同一事务里检查总额是否超限；超限则回滚（等于没预留）。
    """
    from sqlalchemy import text

    from app.services.diagnostics.session import row_lock_clause, session as _db_session

    now = moment or datetime.now(timezone.utc)
    window_start = _window_start(now)
    rt = settings.diagnosis_budget_reserve_tokens
    rc = settings.diagnosis_budget_reserve_cost_usd
    limit_tokens = settings.diagnosis_session_token_budget
    limit_cost = settings.diagnosis_session_cost_budget_usd

    with _db_session() as session:
        lock_clause = row_lock_clause(session)
        session.execute(
            text(_budget_sql_upsert(rt, rc, window_start)),
            {"org": org_id, "uid": user_id, "sid": system_id, "win": window_start,
             "rt": rt, "rc": rc, "now": now},
        )
        row = session.execute(
            text(
                "SELECT reserved_tokens, settled_tokens, reserved_cost, settled_cost "
                "FROM diagnosis_budget_ledger WHERE org_id=:org AND user_id=:uid AND system_id=:sid "
                "AND window_started_at=:win" + lock_clause
            ),
            {"org": org_id, "uid": user_id, "sid": system_id, "win": window_start},
        ).first()
        used_tokens = int(row[0] or 0) + int(row[1] or 0) if row else rt
        used_cost = float(row[2] or 0) + float(row[3] or 0) if row else rc
        ok = used_tokens <= limit_tokens and used_cost <= limit_cost
        if ok:
            session.commit()
        else:
            session.rollback()
        return {
            "allowed": ok,
            "used_tokens": used_tokens,
            "limit_tokens": limit_tokens,
            "used_cost": round(used_cost, 6),
            "limit_cost": limit_cost,
            "window_started_at": window_start.isoformat(),
        }


def settle_budget(
    *,
    org_id: int,
    user_id: int,
    system_id: int,
    tokens: int,
    cost_usd: float,
    moment: datetime | None = None,
) -> None:
    """结算：把预留换成实测（未测到则用保守估算值，见 record_run_usage.settle_tokens）。"""
    from sqlalchemy import text

    from app.services.diagnostics.session import row_lock_clause, session as _db_session

    now = moment or datetime.now(timezone.utc)
    window_start = _window_start(now)
    rt = settings.diagnosis_budget_reserve_tokens
    rc = settings.diagnosis_budget_reserve_cost_usd
    try:
        with _db_session() as session:
            session.execute(
                text(
                    "UPDATE diagnosis_budget_ledger SET "
                    "reserved_tokens = CASE WHEN reserved_tokens >= :rt THEN reserved_tokens - :rt ELSE 0 END, "
                    "reserved_cost   = CASE WHEN reserved_cost   >= :rc THEN reserved_cost   - :rc ELSE 0 END, "
                    "settled_tokens = settled_tokens + :tok, settled_cost = settled_cost + :cost, "
                    "updated_at = :now "
                    "WHERE org_id=:org AND user_id=:uid AND system_id=:sid AND window_started_at=:win"
                ),
                {"org": org_id, "uid": user_id, "sid": system_id, "win": window_start,
                 "rt": rt, "rc": rc, "tok": max(0, int(tokens)), "cost": max(0.0, float(cost_usd)), "now": now},
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001 - 结算失败不该影响诊断收尾
        log.warning("[budget] 结算失败 org=%s user=%s system=%s: %s", org_id, user_id, system_id, exc)


def release_budget(*, org_id: int, user_id: int, system_id: int, moment: datetime | None = None) -> None:
    """未真正开跑就失败 / 收尸时补释放：把预留扣回来（避免"提前退出导致预留泄漏"）。"""
    from sqlalchemy import text

    from app.services.diagnostics.session import row_lock_clause, session as _db_session

    now = moment or datetime.now(timezone.utc)
    window_start = _window_start(now)
    rt = settings.diagnosis_budget_reserve_tokens
    rc = settings.diagnosis_budget_reserve_cost_usd
    try:
        with _db_session() as session:
            session.execute(
                text(
                    "UPDATE diagnosis_budget_ledger SET "
                    "reserved_tokens = CASE WHEN reserved_tokens >= :rt THEN reserved_tokens - :rt ELSE 0 END, "
                    "reserved_cost   = CASE WHEN reserved_cost   >= :rc THEN reserved_cost   - :rc ELSE 0 END, "
                    "updated_at = :now "
                    "WHERE org_id=:org AND user_id=:uid AND system_id=:sid AND window_started_at=:win"
                ),
                {"org": org_id, "uid": user_id, "sid": system_id, "win": window_start,
                 "rt": rt, "rc": rc, "now": now},
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("[budget] 释放预留失败: %s", exc)


def release_for_report(report, *, moment: datetime | None = None) -> None:
    """按报告行释放它当年预留的预算（收尸/关停兜底路径用）。

    预留额是配置常量，所以用报告创建时间定位同一个窗口即可精确扣回。
    """
    try:
        release_budget(
            org_id=report.org_id,
            user_id=report.user_id or 0,
            system_id=report.system_id,
            moment=moment or _aware(getattr(report, "created_at", None)),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[budget] 按报告释放预留失败 report=%s: %s", getattr(report, "id", "?"), exc)


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


__all__ = [
    "assess_completeness",
    "parse_usage",
    "record_run_usage",
    "session_usage",
    "reserve_budget",
    "settle_budget",
    "release_budget",
    "release_for_report",
    "GAP_IN_FLIGHT",
    "GAP_PROVIDER",
    "GAP_PROCESS_KILLED",
]

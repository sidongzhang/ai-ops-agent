"""收尸（reaper）：处理"没人再推进"的诊断。

两种来源：
  * 服务被 SIGKILL / 关停宽限期耗尽 → 报告永远停在 `running`；
  * 用户点了停止但运行方已失联（`cancel_requested_at` 有值却无人落终态）。

处理：
  * 有 `cancel_requested_at` → 落 `cancelled`（用户意图明确）；
  * 否则 → 落 `failed`，错误信息写明"执行方已失联"；
  * 两者都按"是否拿到可恢复历史"设 `resumable`，**绝不谎称可继续**。
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.core.config import settings
from app.models.diagnostics import DiagnosisReport

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def reap_stale_diagnoses(*, minutes: int | None = None, limit: int = 200) -> dict:
    """把超时未推进的诊断收成终态。返回 {"scanned": n, "cancelled": n, "failed": n}。"""
    from app.core.database import engine
    from app.models.run_state import DiagnosisRunState

    window = settings.diagnosis_stale_running_minutes if minutes is None else minutes
    cutoff = utcnow() - timedelta(minutes=max(1, window))
    result = {"scanned": 0, "cancelled": 0, "failed": 0, "unresumable": 0}
    try:
        with Session(engine) as session:
            rows = session.exec(
                select(DiagnosisReport)
                .where(DiagnosisReport.status == "running")
                .where(DiagnosisReport.created_at < cutoff)
                .limit(limit)
            ).all()
            for report in rows:
                result["scanned"] += 1
                state = session.get(DiagnosisRunState, report.id)
                has_history = bool(state and state.messages_json)
                now = utcnow()
                if report.cancel_requested_at is not None:
                    report.status = "cancelled"
                    report.cancelled_at = now
                    report.answer = report.answer or "（诊断已被用户中断，运行方未及落库）"
                    result["cancelled"] += 1
                else:
                    report.status = "failed"
                    report.error_message = "执行方已失联（服务重启或进程异常退出）"
                    report.answer = report.answer or "诊断未能完成：执行方已失联"
                    result["failed"] += 1
                session.add(report)
                if state is None:
                    session.add(DiagnosisRunState(report_id=report.id, messages_json="", resumable=False))
                    result["unresumable"] += 1
                elif not has_history:
                    state.resumable = False
                    session.add(state)
                    result["unresumable"] += 1
            session.commit()
    except Exception as exc:  # noqa: BLE001 - 收尸失败不能影响启动
        log.warning("[reaper] 收尸失败: %s", exc)
        return result
    if result["scanned"]:
        log.warning(
            "[reaper] 收尸 %s 条（cancelled=%s failed=%s 不可恢复=%s）",
            result["scanned"], result["cancelled"], result["failed"], result["unresumable"],
        )
    return result

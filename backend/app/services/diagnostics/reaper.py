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
    """把**确实失联**的诊断收成终态。返回 {"scanned","cancelled","failed","unresumable"}。

    失联判定（关键：不能只看创建时间，否则会误收其他 worker/实例的**活跃任务**）：
      1. 有心跳 → 只有心跳超过 `diagnosis_stale_heartbeat_seconds` 才算失联（worker 每轮巡检续期）；
      2. 无心跳（从未注册成功就死了）→ 退回创建时间，但窗口更短；
      3. 心跳新鲜 → **一律跳过**（哪怕报告已经跑了很久）。
    """
    from app.models.run_state import DiagnosisRunState
    from app.services.diagnostics.session import session as _db_session

    window_minutes = settings.diagnosis_stale_running_minutes if minutes is None else minutes
    heartbeat_seconds = settings.diagnosis_stale_heartbeat_seconds
    now = utcnow()
    created_cutoff = now - timedelta(minutes=max(1, window_minutes))
    heartbeat_cutoff = now - timedelta(seconds=max(5, heartbeat_seconds))
    result = {"scanned": 0, "cancelled": 0, "failed": 0, "unresumable": 0, "alive": 0}
    try:
        with _db_session() as session:
            rows = session.exec(
                select(DiagnosisReport)
                .where(DiagnosisReport.status == "running")
                .limit(limit)
            ).all()
            for report in rows:
                heartbeat = report.runner_heartbeat_at
                if heartbeat is not None:
                    if heartbeat.tzinfo is None:
                        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
                    if heartbeat >= heartbeat_cutoff:
                        result["alive"] += 1          # 别的 worker 还在推进 → 绝不误收
                        continue
                elif report.created_at is not None:
                    created = report.created_at
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    if created >= created_cutoff:
                        continue                      # 刚建、还没注册 → 再等等
                result["scanned"] += 1
                state = session.get(DiagnosisRunState, report.id)
                has_history = bool(state and state.messages_json)
                if report.cancel_requested_at is not None:
                    report.status = "cancelled"
                    report.cancelled_at = now
                    report.answer = report.answer or "（诊断已被用户中断，运行方未及落库）"
                    result["cancelled"] += 1
                else:
                    report.status = "failed"
                    report.error_message = "执行方已失联（心跳超时：服务重启或进程异常退出）"
                    report.answer = report.answer or "诊断未能完成：执行方已失联"
                    result["failed"] += 1
                session.add(report)
                # 释放该报告当年预留的预算（否则提前退出的诊断会一直占着会话预算）
                from app.services.diagnostics.usage import release_for_report

                release_for_report(report, moment=now)
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
            "[reaper] 收尸 %s 条（cancelled=%s failed=%s 不可恢复=%s；跳过活跃 %s）",
            result["scanned"], result["cancelled"], result["failed"],
            result["unresumable"], result["alive"],
        )
    return result

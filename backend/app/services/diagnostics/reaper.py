"""收尸（reaper）：处理"没人再推进"的诊断。

两种来源：
  * 服务被 SIGKILL / 关停宽限期耗尽 → 报告永远停在 `running`；
  * 用户点了停止但运行方已失联（`cancel_requested_at` 有值却无人落终态）。

处理：
  * 失联只证明没有可靠收尾，统一落 failed，停止状态标未知；
  * 无论用户是否点过取消、是否有历史，都禁止继续。
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlmodel import select
from sqlalchemy import String, cast

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
    from app.services.diagnostics.session import session as _db_session

    window_minutes = settings.diagnosis_stale_running_minutes if minutes is None else minutes
    heartbeat_seconds = settings.diagnosis_stale_heartbeat_seconds
    now = utcnow()
    created_cutoff = now - timedelta(minutes=max(1, window_minutes))
    heartbeat_cutoff = now - timedelta(seconds=max(5, heartbeat_seconds))
    result = {"scanned": 0, "cancelled": 0, "failed": 0, "unresumable": 0, "alive": 0}
    claimed_ids=[]
    try:
        with _db_session() as session:
            rows = session.exec(
                select(DiagnosisReport,cast(DiagnosisReport.runner_heartbeat_at,String).label('heartbeat_token'))
                .where(DiagnosisReport.status == "running")
                .limit(limit)
            ).all()
            for report,heartbeat_token in rows:
                from app.models.diagnosis_jobs import DiagnosisJob
                job = session.get(DiagnosisJob, report.id)
                if job and job.state == "pending":
                    continue  # Durable queue reconciler owns unstarted work.
                if job and job.state == "started" and report.runner_heartbeat_at is None:
                    began = job.started_at
                    if began is not None:
                        began = began.replace(tzinfo=timezone.utc) if began.tzinfo is None else began
                        if began >= created_cutoff:
                            continue
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
                from app.services.diagnostics.usage import record_run_usage, settle_budget
                from app.services.diagnostics.termination import mark_execution_unknown
                if not mark_execution_unknown(session,report,'process_killed',check_heartbeat=True,
                                              heartbeat_token=heartbeat_token):
                    continue
                claimed_ids.append(report.id)
                reservation_id=(report.business_context or {}).get("budget_reservation_id","")
                if reservation_id:
                    # An owner was admitted. Unknown consumption is not refunded as free.
                    summary=record_run_usage(report_id=report.id,org_id=report.org_id,system_id=report.system_id,
                        usage_json='{"usage_complete":false,"cost_complete":false,"gap_reason":"process_killed"}',
                        before_json="",session=session,commit=False)
                    from app.models.usage import DiagnosisBudgetReservation
                    reservation=session.get(DiagnosisBudgetReservation,reservation_id)
                    if not summary["usage_complete"] and reservation is not None:
                        summary["settle_tokens"]=max(summary["tokens_delta"],reservation.reserved_tokens)
                        summary["settle_cost"]=max(summary["cost_delta"],reservation.reserved_cost)
                    settle_budget(org_id=report.org_id,user_id=report.user_id or 0,system_id=report.system_id,
                        reservation_id=reservation_id,tokens=summary["settle_tokens"],cost_usd=summary["settle_cost"],session=session)
                else:
                    # Without an immutable reservation/window identity we cannot
                    # safely refund or move the amount into another time window.
                    record_run_usage(report_id=report.id,org_id=report.org_id,system_id=report.system_id,
                        usage_json='{"usage_complete":false,"cost_complete":false,"gap_reason":"process_killed"}',
                        before_json='',session=session,commit=False)
            session.commit()
            result['scanned']=result['failed']=result['unresumable']=len(claimed_ids)
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

"""诊断的优雅关停：主动取消在跑的诊断，并**有界等待**它们把可恢复历史落库。

为什么需要：`BackgroundTasks` 里的 `run_sync` 阻塞在工作线程；uvicorn 收到 SIGTERM 后
默认**不会**等我们落库就退出 → 历史丢失、报告永久停在 running。这里：
  1. 标记 shutting_down（拒绝新提交，见 API 层）；
  2. 对本进程注册表内所有 in-flight token 调 `token.cancel()`
     （官方：这是中断阻塞中的 run_sync 的唯一方式）；
  3. 按 `diagnosis_shutdown_grace_seconds` 有界等待工作线程退出，再核对数据库；
  4. 仍在 running → 标记失败、停止未确认、不可继续，保留费用预留。

取消令牌只表达请求；只有运行方确认执行结束并保存有效历史，才能声明可继续。
硬杀（SIGKILL）只能靠 reaper；数据库不可用时关停写入也可能失败。
"""
import asyncio
import logging
import time

from app.core.config import settings

log = logging.getLogger(__name__)


async def graceful_shutdown_diagnoses() -> dict:
    from app.services.diagnostics import cancel_bus

    cancel_bus.mark_shutting_down()
    grace = settings.diagnosis_shutdown_grace_seconds
    started = time.monotonic()
    result = await asyncio.to_thread(shutdown_sync, grace)
    result["grace_seconds"] = grace
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result


def shutdown_sync(grace_seconds: float | None = None) -> dict:
    """同步版关停：取消在跑诊断 → 有界等待落库 → **自己给未落库的写终态**。

    信号处理器与 lifespan 都走这里。有界等待线程收尾后核对数据库；
    数据库不可用或进程被硬杀时，仍需启动后的 reaper 补收尾。
    """
    from app.services.diagnostics import cancel_bus

    grace = settings.diagnosis_shutdown_grace_seconds if grace_seconds is None else grace_seconds
    result = cancel_bus.cancel_all_and_wait(grace)
    result['workers_finished'] = result['persisted']
    result['persisted'] = 0
    result['unpersisted'] = []
    # done means the worker exited, not that its final DB write succeeded.
    for report_id in result.get("report_ids", []) or []:
        try:
            if _force_terminal_on_shutdown(report_id):
                result['persisted'] += 1
            else:
                result['unpersisted'].append(report_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("[shutdown] 写终态失败 report=%s: %s", report_id, exc)
            result['unpersisted'].append(report_id)
    if result["cancelled"]:
        log.info(
            "[shutdown] 诊断关停：cancelled=%s persisted=%s unpersisted=%s grace=%ss",
            result["cancelled"], result["persisted"], result["unpersisted"], grace,
        )
    return result


def _force_terminal_on_shutdown(report_id: int) -> bool:
    """关停宽限期耗尽只证明未完成收尾，不能推断已停止或允许继续。"""
    from sqlmodel import select
    from app.models.diagnostics import DiagnosisReport
    from app.services.diagnostics.session import session as db_session
    from app.services.diagnostics.termination import mark_execution_unknown

    with db_session() as session:
        report=session.exec(select(DiagnosisReport).where(DiagnosisReport.id==report_id).with_for_update()).first()
        if report is None:
            return False
        if report.status!='running':
            return True
        from app.services.diagnostics import cancel_bus
        if report.runner_id not in ('',None,cancel_bus.INSTANCE_ID):
            return False
        if not mark_execution_unknown(session,report,'shutdown_execution_unknown'):
            return False
        # Keep the reservation for the existing accounting reconciler. Grace
        # expiry does not prove zero consumption or authorize a budget refund.
        session.commit()
    log.warning('[shutdown] report=%s 停止未确认，已标记失败且不可继续',report_id)
    return True

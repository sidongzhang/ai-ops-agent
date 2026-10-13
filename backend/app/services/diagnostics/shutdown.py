"""诊断的优雅关停：主动取消在跑的诊断，并**有界等待**它们把可恢复历史落库。

为什么需要：`BackgroundTasks` 里的 `run_sync` 阻塞在工作线程；uvicorn 收到 SIGTERM 后
默认**不会**等我们落库就退出 → 历史丢失、报告永久停在 running。这里：
  1. 标记 shutting_down（拒绝新提交，见 API 层）；
  2. 对本进程注册表内所有 in-flight token 调 `token.cancel()`
     （官方：这是中断阻塞中的 run_sync 的唯一方式）；
  3. 按 `diagnosis_shutdown_grace_seconds` 有界等待"已落库"事件；
  4. 宽限期到仍未落库 → 标记 `resumable=false`（不谎称可继续）并写审计。

注意：这里拿到的是**我们主动取消的 token** → 运行方抛 `RunCancelled`（可恢复）；
硬杀（SIGKILL）拿不到任何东西，只能靠 reaper。
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

    信号处理器（不能阻塞）与 lifespan 都走这里，保证无论进程何时退出，
    报告都不会停留在 running。
    """
    from app.services.diagnostics import cancel_bus

    grace = settings.diagnosis_shutdown_grace_seconds if grace_seconds is None else grace_seconds
    result = cancel_bus.cancel_all_and_wait(grace)
    for report_id in result.get("unpersisted", []) or []:
        try:
            _force_terminal_on_shutdown(report_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("[shutdown] 写终态失败 report=%s: %s", report_id, exc)
    if result["cancelled"]:
        log.info(
            "[shutdown] 诊断关停：cancelled=%s persisted=%s unpersisted=%s grace=%ss",
            result["cancelled"], result["persisted"], result["unpersisted"], grace,
        )
    return result


def _force_terminal_on_shutdown(report_id: int) -> None:
    """把关停时未落库的诊断收成 cancelled + resumable=false（无历史可取）。"""
    from datetime import datetime, timezone

    from sqlmodel import Session

    from app.core.database import engine
    from app.models.diagnostics import DiagnosisReport
    from app.models.run_state import DiagnosisRunState
    from app.services.diagnostics import cancel_bus

    with Session(engine) as session:
        report = session.get(DiagnosisReport, report_id)
        if report is None or report.status != "running":
            return
        report.status = "cancelled"
        report.cancelled_at = datetime.now(timezone.utc)
        report.cancel_phase = cancel_bus.current_phase(report_id) or report.cancel_phase or "idle"
        report.answer = report.answer or "（服务关停，诊断已中断；本次未取得可恢复历史）"
        session.add(report)
        state = session.get(DiagnosisRunState, report_id)
        if state is None:
            state = DiagnosisRunState(report_id=report_id, messages_json="", resumable=False)
        else:
            state.resumable = False
        session.add(state)
        session.commit()
        from app.services.diagnostics.usage import release_for_report

        release_for_report(report)
    log.warning("[shutdown] report=%s 已标记 cancelled 且不可恢复（关停时未落库）", report_id)

"""可恢复运行状态的读写（框架原生消息历史 + 框架累计用量）。

只存一份，与报告行分离（历史 JSON 可能很大，列表接口不需要）。
`resumable=False` 表示进程被硬杀/关停宽限期耗尽，历史没拿到——对外不得宣称"可继续"。
"""
import logging

from sqlmodel import Session

from app.models.run_state import DiagnosisRunState

log = logging.getLogger(__name__)


def save_run_state(
    report_id: int,
    *,
    messages_json: str = "",
    conversation_id: str = "",
    run_id: str = "",
    usage_json: str = "",
    resumable: bool = True,
) -> None:
    """落库（upsert）。失败静默：状态保存绝不能拖垮诊断主流程。"""
    if not messages_json:
        return
    try:
        from app.core.database import engine

        with Session(engine) as session:
            row = session.get(DiagnosisRunState, report_id)
            if row is None:
                row = DiagnosisRunState(report_id=report_id)
            row.messages_json = messages_json
            row.conversation_id = conversation_id or row.conversation_id
            row.run_id = run_id or row.run_id
            row.framework_cumulative_json = usage_json or row.framework_cumulative_json
            row.resumable = resumable
            session.add(row)
            # 关停时我们可能先写过"未取得可恢复历史"的兜底文案；若历史随后落库，
            # 把文案纠正过来，避免"说有历史/说没历史"自相矛盾。
            from app.models.diagnostics import DiagnosisReport

            report = session.get(DiagnosisReport, report_id)
            if (
                report is not None
                and report.status == "cancelled"
                and "未取得可恢复历史" in (report.answer or "")
            ):
                report.answer = "（诊断已被中断；已保留可恢复历史，可继续）"
                session.add(report)
            session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("[run_state] 保存失败 report=%s: %s", report_id, exc)


def load_run_state(report_id: int, session: Session | None = None) -> DiagnosisRunState | None:
    from app.core.database import engine

    if session is not None:
        return session.get(DiagnosisRunState, report_id)
    with Session(engine) as own:
        return own.get(DiagnosisRunState, report_id)


def mark_unresumable(report_id: int) -> None:
    """硬杀/关停超时：把运行状态标记为不可恢复（不谎称可继续）。

    注意：这只改 `diagnosis_run_state`，不动报告终态；报告终态由
    `services.diagnostics.shutdown._force_terminal_on_shutdown` 或 reaper 负责。
    """
    try:
        from app.core.database import engine

        with Session(engine) as session:
            row = session.get(DiagnosisRunState, report_id)
            if row is None:
                row = DiagnosisRunState(report_id=report_id, messages_json="")
                session.add(row)
            row.resumable = False
            session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("[run_state] 标记不可恢复失败 report=%s: %s", report_id, exc)

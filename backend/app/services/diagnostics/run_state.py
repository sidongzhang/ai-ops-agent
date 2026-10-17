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
    engine: str = "pydantic",
    engine_version: str = "",
    history_schema_version: int = 1,
) -> bool:
    """落库（upsert）。**返回是否真的存下来了**。

    失败必须如实返回 False（调用方据此把 `resumable` 标成不可恢复）：
    "历史保存失败却宣称可继续"会把用户带进一个必然失败的 resume。
    """
    if not messages_json:
        return False
    try:
        from app.agent.runtime.contracts import RuntimeCheckpoint
        from app.agent.runtime.factory import get_runtime
        runtime = get_runtime(engine)
        resumable = resumable and runtime.capabilities().resume
        runtime.validate_checkpoint(RuntimeCheckpoint(
            messages_json, usage_json, engine, engine_version, history_schema_version,
        ))
        from app.core.database import engine as db_engine

        with Session(db_engine) as session:
            from app.models.diagnostics import DiagnosisReport
            from sqlmodel import select
            report=session.exec(select(DiagnosisReport).where(DiagnosisReport.id==report_id).with_for_update()).first()
            if report is None:
                return False
            row = session.get(DiagnosisRunState, report_id)
            if row is None:
                row = DiagnosisRunState(report_id=report_id)
            row.messages_json = messages_json
            row.conversation_id = conversation_id or row.conversation_id
            row.run_id = run_id or row.run_id
            row.framework_cumulative_json = usage_json or row.framework_cumulative_json
            row.engine = engine
            row.engine_version = engine_version
            row.history_schema_version = history_schema_version
            resumable = resumable and not (report.business_context or {}).get("execution_stop_unconfirmed",False)
            row.resumable = resumable
            session.add(row)
            # 关停时我们可能先写过"未取得可恢复历史"的兜底文案；若历史随后落库，
            # 把文案纠正过来，避免"说有历史/说没历史"自相矛盾。
            from app.models.diagnostics import DiagnosisReport

            report = session.get(DiagnosisReport, report_id)
            if (
                resumable
                and report is not None
                and report.status == "cancelled"
                and "未取得可恢复历史" in (report.answer or "")
            ):
                report.answer = "（诊断已被中断；已保留可恢复历史，可继续）"
                session.add(report)
            session.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("[run_state] 保存失败 report=%s（该报告将标为不可恢复）: %s", report_id, exc)
        _mark_unresumable_quiet(report_id)
        return False


def _mark_unresumable_quiet(report_id: int) -> None:
    """尽力把 resumable 标成 false（保存失败时不能留一个"看起来能继续"的状态）。"""
    try:
        from app.core.database import engine

        with Session(engine) as session:
            from app.models.diagnostics import DiagnosisReport
            if session.get(DiagnosisReport,report_id) is None:
                return
            row = session.get(DiagnosisRunState, report_id)
            if row is None:
                row = DiagnosisRunState(report_id=report_id, messages_json="", resumable=False)
            else:
                row.resumable = False
            session.add(row)
            session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("[run_state] 标记不可恢复也失败 report=%s: %s", report_id, exc)


def resumable_of(report_id: int, session: Session | None = None) -> bool:
    """读取时计算"是否真的可继续"：历史存在**且能反序列化**才算。

    不信任历史里的 `resumable` 标记本身——保存失败/写坏 JSON 都必须反映成 false，
    否则就等于对用户宣称"可继续"，而 resume 必然失败。
    """
    from app.models.diagnostics import DiagnosisReport
    from app.services.diagnostics.session import session as db_session
    if session is None:
        with db_session() as current:
            return resumable_of(report_id,session=current)
    report=session.get(DiagnosisReport,report_id)
    if report is None or (report.business_context or {}).get('execution_stop_unconfirmed',False):
        return False
    state = load_run_state(report_id, session=session)
    if state is None or not state.resumable or not state.messages_json:
        return False
    try:
        from app.agent.runtime.contracts import RuntimeCheckpoint
        from app.agent.runtime.factory import get_runtime
        runtime = get_runtime(state.engine)
        if not runtime.capabilities().resume:
            return False
        getattr(runtime,"validate_resume_checkpoint",runtime.validate_checkpoint)(RuntimeCheckpoint(
            state.messages_json, state.framework_cumulative_json,
            state.engine, state.engine_version, state.history_schema_version,
        ))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("[run_state] report=%s 历史反序列化失败（视为不可恢复）: %s", report_id, exc)
        return False


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

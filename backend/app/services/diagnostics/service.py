"""Application services for AI diagnosis and diagnostic playbooks."""
import json
import logging
import time
from datetime import datetime, timezone

from sqlmodel import Session

from app.agent.runtime.entrypoint import diagnose_with_details
from app.agent.runtime.contracts import RuntimeCompatibilityError, RuntimeBudgetExceeded, RuntimeExecutionError
from app.agent.runtime.factory import get_runtime
from app.agent.diagnostics.skill_router import match_skill
from app.agent.diagnostics.knowledge.store import get_relevant_context_with_memories as get_relevant_context
from app.repositories.systems import list_enabled_services_for_system
from app.services.collectors.exec import select_online_collector
from app.schemas import DiagnoseResponse
from app.services.audit import record_audit_event
from app.agent.harness.gateway import ToolAuditError
from app.services.data_analysis import (
    analyze_system_data,
    describe_readonly_datasets,
    get_dataset_source_config,
    get_readonly_database_config,
    list_readonly_datasets,
    query_readonly_dataset,
)
from app.services.data_analysis import _remote_query_executor
from app.services.descriptors.builder import system_to_descriptor
from app.services.diagnostics.evidence import (
    TOOL_LABELS,
    build_evidence_from_tool_calls,
    build_evidence_steps,
    build_knowledge_refs,
    summarize_text,
)
from app.models.diagnostics import DiagnosisReport
from app.services.diagnostics.kafka_fallback import (
    is_kafka_lag_question as _is_kafka_lag_question,
    kafka_lag_fallback as _kafka_lag_fallback,
)
from app.services.diagnostics.responses import build_diagnose_response as _build_diagnose_response
from app.services.diagnostics.templates import disabled_template_names as _disabled_template_names
# 模板管理已拆到 templates.py，这里 re-export 保持既有 import 路径不变。
from app.services.diagnostics.templates import (
    list_diagnostic_templates,
    update_diagnostic_templates,
)
from app.services.diagnostics.reports import save_diagnosis_report
from app.services.systems.service import require_system
from app.services.realtime.websocket import manager

log = logging.getLogger(__name__)


def _evidence_sources(steps: str, tool_calls: list[dict]) -> list[str]:
    combined = " ".join([steps, *(call.get("tool", "") for call in tool_calls)])
    source_map = (
        ("服务健康检查", ("check_service", "list_services")),
        ("Prometheus 指标", ("query_prometheus",)),
        ("服务日志", ("read_logs", "search_logs")),
        ("Redis 运行数据", ("run_redis_command",)),
        ("Kafka 运行数据", ("run_kafka_command",)),
        ("运维知识库", ("search_knowledge_base",)),
    )
    return [label for label, markers in source_map if any(marker in combined for marker in markers)]


def _remote_command(session: Session, system):
    if system.local:
        return None
    collector = select_online_collector(session, system.id)
    if not collector or not manager.is_connected(collector.id):
        return None

    def execute(command: str, args: dict) -> dict:
        try:
            return manager.send_command_sync(collector.id, command, args)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "result": str(exc)}

    return execute


def _archive_access(session: Session, system, org_id: int):
    """构建 agent 的归档只读工具（列文件 / 读内容），经采集器在 ALGP 侧执行。

    归档根目录取自 system.infra.archive.base_path；具体目录按
    <base>/<YYYY>/<MM>/<tcatId> 在采集器侧解析并做白名单校验。
    """
    from app.core.security import decrypt_sensitive_fields

    if system.local:
        return None, None, None, None
    infra = decrypt_sensitive_fields(system.infra or {})
    base_path = str((infra.get("archive") or {}).get("base_path") or "").strip()
    if not base_path:
        return None, None, None, None
    collector = select_online_collector(session, system.id)
    if not collector or not manager.is_connected(collector.id):
        return None, None, None, None

    def _send(command: str, args: dict) -> dict:
        try:
            return manager.send_command_sync(collector.id, command, args)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "result": str(exc)}

    def list_files(tcat_id: str) -> str:
        resp = _send("archive_list", {"base_path": base_path, "tcat_id": tcat_id})
        data = resp.get("result") if resp.get("ok") else None
        if not isinstance(data, dict):
            return f"归档文件列举失败：{resp.get('result')}"
        files = data.get("files") or []
        if not files:
            return f"未找到归档文件（目录：{data.get('archive_dir') or '未知'}）。"
        lines = [f"归档目录：{data.get('archive_dir')}", f"共 {data.get('total', len(files))} 个文件："]
        for item in files:
            lines.append(
                f"- {item.get('name')}（{item.get('kind', '')}，{item.get('size', 0)} 字节，{item.get('mtime', '')}）"
            )
        return "\n".join(lines)

    def read_file(tcat_id: str, file_name: str) -> str:
        resp = _send("archive_read", {"base_path": base_path, "tcat_id": tcat_id, "file_name": file_name})
        data = resp.get("result") if resp.get("ok") else None
        if not isinstance(data, dict):
            return f"读取归档文件失败：{resp.get('result')}"
        head = f"文件 {data.get('name')}（{data.get('format')}，{data.get('size')} 字节）"
        if data.get("format") == "fits":
            return f"{head}\nFITS 头：\n{data.get('header') or '(空)'}"
        return f"{head}\n内容：\n{data.get('text') or '(空)'}"

    def raw_fetch(tcat_id: str, file_name: str):
        resp = _send("archive_fetch", {"base_path": base_path, "tcat_id": tcat_id, "file_name": file_name})
        data = resp.get("result") if resp.get("ok") else None
        if not isinstance(data, dict):
            return None
        import base64

        try:
            raw = base64.b64decode(data.get("content") or "")
        except Exception:  # noqa: BLE001
            return None
        return (data.get("name") or file_name), raw

    def fetch_file(tcat_id: str, file_name: str) -> str:
        from app.core.config import settings as app_settings
        from app.services.diagnostics.artifacts import save_artifact

        got = raw_fetch(tcat_id, file_name)
        if not got:
            return "取回归档文件失败（不可用或超过大小限制）。"
        name, raw = got
        token = save_artifact(name, raw)
        base_url = (app_settings.public_base_url or "").rstrip("/")
        url = f"{base_url}/systems/{system.id}/diagnosis-artifacts/{token}"
        return f"已取回文件 {name}（{len(raw)} 字节）。下载链接：{url}"

    return list_files, read_file, fetch_file, raw_fetch


def _code_sandbox(system, raw_fetch):
    """构建 agent 的 Python 沙箱工具：可放入归档文件、跑代码、把产物存成下载链接。"""
    if not raw_fetch:
        return None

    def run_code(code: str, inputs: list | None = None) -> str:
        from app.core.config import settings as app_settings
        from app.services.diagnostics.artifacts import save_artifact
        from app.services.diagnostics.sandbox import run_python

        staged: dict[str, bytes] = {}
        notes: list[str] = []
        for item in (inputs or [])[:5]:
            if not isinstance(item, dict):
                continue
            got = raw_fetch(str(item.get("tcat_id") or ""), str(item.get("file_name") or ""))
            if not got:
                notes.append(f"未能取回 {item.get('file_name')}（不存在或超过大小限制）")
                continue
            name, raw = got
            staged[name] = raw
            notes.append(f"{name}（{len(raw)} 字节）")
        if notes:
            notes.insert(0, "已放入工作目录的输入文件：" + "；".join(notes) + "。")

        result = run_python(code, staged)
        parts = list(notes)
        if result.get("stdout"):
            parts.append("代码输出：\n" + result["stdout"].strip())
        if result.get("stderr"):
            parts.append("stderr：\n" + result["stderr"].strip()[:1500])
        links = []
        base_url = (app_settings.public_base_url or "").rstrip("/")
        for name, data in (result.get("outputs") or {}).items():
            token = save_artifact(name, data)
            links.append(f"- {name}（{len(data)} 字节）：{base_url}/systems/{system.id}/diagnosis-artifacts/{token}")
        if links:
            parts.append("生成的产物（可直接给用户下载）：\n" + "\n".join(links))
        if not result.get("ok") and not result.get("stdout"):
            parts.append("（脚本执行失败，请检查代码或改用更小/更简单的输入）")
        return "\n\n".join(parts) or "（无输出）"

    return run_code


def _business_data_query(session: Session, system, org_id: int, actor_id: str):
    config = get_readonly_database_config(session, system.id, org_id)
    if not config.enabled:
        return None
    system_id = system.id

    def query(question: str) -> str:
        # 工具可能被并行调用，绝不能复用诊断主线程的 Session。
        from app.core.database import engine

        with Session(engine) as scoped:
            try:
                result = analyze_system_data(
                    scoped,
                    system_id,
                    org_id,
                    question,
                    actor_type="agent",
                    actor_id=actor_id or "agent",
                )
            except Exception as exc:  # noqa: BLE001
                return f"无法查询业务数据：{exc}"
        parts = [result.answer]
        evidence = result.evidence or {}
        if evidence.get("analysis_type") == "stuck_tasks":
            parts.append(
                f"卡住任务数：{evidence.get('stuck_count', 0)}，"
                f"阈值：{evidence.get('stuck_threshold_minutes', 0)} 分钟"
            )
        elif evidence.get("total") is not None:
            parts.append(f"统计总量：{evidence.get('total')}")
        return "\n".join(parts)

    return query


def _business_dataset_query(session: Session, system, org_id: int, actor_id: str):
    """Build the agent's dataset tool + prompt catalog from the readonly source config."""
    config = get_dataset_source_config(session, system.id, org_id)
    if not config or config.get("enabled") is False:
        return None, ""
    datasets = list_readonly_datasets(config)
    if not datasets:
        return None, ""
    catalog = describe_readonly_datasets(config)
    remote_executor = _remote_query_executor(session, system)
    max_rows = int(config.get("max_rows") or 60)

    def query(dataset: str, date_from: str = "", date_to: str = "", filters: dict | None = None) -> str:
        try:
            result = query_readonly_dataset(
                config,
                dataset,
                date_from=date_from,
                date_to=date_to,
                filters=filters or None,
                max_rows=max_rows,
                executor=remote_executor,
            )
        except Exception as exc:  # noqa: BLE001
            return f"业务数据集查询失败：{exc}"
        meta = result["dataset"]
        rows = result["rows"]
        header = f"数据集 {meta['code']}（{meta['label']}）"
        if not rows:
            return f"{header} 在 {date_from or '不限'} ~ {date_to or '不限'} 区间内没有任何记录。"
        payload = json.dumps(rows[:40], ensure_ascii=False, default=str)
        totals: dict[str, float] = {}
        for row in rows[:40]:
            for key, value in row.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                totals[key] = totals.get(key, 0) + value
        total_line = ""
        if totals:
            rendered = "，".join(
                f"{key} 合计 {int(value) if float(value).is_integer() else round(value, 2)}"
                for key, value in totals.items()
            )
            total_line = (
                f"\n汇总（对以上 {min(len(rows), 40)} 行求和，可直接引用，不要自己再加减）：{rendered}"
            )
        # 审计用独立 Session：工具会被并行调用，复用诊断主 Session 会污染事务。
        try:
            from app.core.database import engine

            with Session(engine) as audit_session:
                record_audit_event(
                    audit_session,
                    org_id=org_id,
                    system_id=system.id,
                    actor_type="agent",
                    actor_id=actor_id or "agent",
                    event_type="data_analysis.dataset_queried",
                    target_type="dataset",
                    target_id=meta["code"],
                    input={"date_from": date_from, "date_to": date_to},
                    output={"rows": len(rows), "sql": result["sql"]},
                    commit=True,
                )
        except Exception:  # noqa: BLE001
            pass
        return f"{header} 共 {len(rows)} 行（最多展示 40 行）：\n{payload}{total_line}"

    return query, catalog


def _user_id_from_actor(actor_id: str) -> int | None:
    if not actor_id:
        return None
    try:
        return int(actor_id)
    except ValueError:
        return None


def _progress_recorder(report_id: int, org_id: int, system_id: int):
    """Persist each tool call into the report's evidence list while it runs.

    Tool events update evidence; user-facing text has a separate bounded stream.
    Observer failures must not change diagnosis execution or trigger fallbacks.
    """
    from app.core.database import engine
    from .answer_stream import AnswerRecorder
    answers = AnswerRecorder(report_id, org_id, system_id)

    def record(event: dict) -> None:
        if event.get("kind") == "tool_start":
            answers.record({"kind": "answer_tools"})
        if str(event.get("kind", "")).startswith("answer_"):
            answers.record(event)
            return
        try:
            with Session(engine) as session:
                report = session.get(DiagnosisReport, report_id)
                if not report or report.status != "running":
                    return
                if report.org_id != org_id or report.system_id != system_id:
                    return
                items = [dict(item) for item in (report.evidence or [])]
                call_id = str(event.get("call_id") or "")
                tool = str(event.get("tool") or "unknown")
                index = next(
                    (i for i, item in enumerate(items) if call_id and item.get("call_id") == call_id),
                    -1,
                )
                if event.get("kind") == "tool_start":
                    patch = {
                        "step": (index + 1) if index >= 0 else len(items) + 1,
                        "type": tool,
                        "label": TOOL_LABELS.get(tool, tool),
                        "detail": "执行中…",
                        "status": "started",
                        "duration_ms": 0,
                        "input": event.get("input") or {},
                        "output": "",
                        "call_id": call_id,
                    }
                else:
                    output = str(event.get("output") or "")
                    patch = {
                        "status": str(event.get("status") or "success"),
                        "duration_ms": int(event.get("duration_ms") or 0),
                        "detail": summarize_text(output) or "已完成",
                        "output": output[:2000],
                    }
                if index >= 0:
                    items[index].update(patch)
                else:
                    items.append(
                        {
                            "step": len(items) + 1,
                            "type": tool,
                            "label": TOOL_LABELS.get(tool, tool),
                            "input": {},
                            "call_id": call_id,
                            **patch,
                        }
                    )
                report.evidence = items
                session.add(report)
                session.commit()
        except Exception as exc:  # noqa: BLE001
            log.debug("[diagnose] 进度写入失败（不影响诊断）: %s", exc)

    record.close = answers.close
    return record


def start_diagnosis(
    session: Session,
    system_id: int,
    org_id: int,
    question: str,
    *,
    actor_id: str = "",
    external_request_id: str = "",
    business_context: dict | None = None,
    chat_id: str = "",
    submission_id: str | None = None,
    job_question: str | None = None,
    job_options: dict | None = None,
) -> DiagnoseResponse:
    system = require_system(session, system_id, org_id)
    if not chat_id and business_context:
        business_context = {key: value for key, value in business_context.items() if key != "chat_id"}
    if chat_id:
        from .chats import attach_chat
        attach_chat(session, system_id, org_id, chat_id, question)
        business_context = {**(business_context or {}), "chat_id": chat_id}
    from .routing import decision,NeedsDiagnosis,recent_messages
    route=decision(session,system_id,org_id,question,chat_id=chat_id,
        special=bool(external_request_id or (job_options or {}).get('images') or
            {k:v for k,v in (business_context or {}).items() if k not in ('chat_id','runtime_engine','interaction','budget_reservation_id') and v}))
    if route['resolved']=='blocked':raise NeedsDiagnosis(route)
    business_context={**(business_context or {}),'interaction':route}
    job_options={**(job_options or {}),'interaction_kind':route['resolved'],
        'chat_context':recent_messages(session,system_id,org_id,chat_id) if route['resolved']=='chat' else []}
    if route['resolved']=='chat':job_question=question
    # 会话预算：提交前**原子预留**（并发安全）。未知用量按保守预留计入，绝不当免费。
    from app.services.diagnostics.usage import BudgetExceeded, reserve_budget

    user_id = _user_id_from_actor(actor_id) or 0
    budget = reserve_budget(org_id=org_id, user_id=user_id, system_id=system.id, transaction=session)
    if not budget["allowed"]:
        raise BudgetExceeded(
            "会话预算不足（滚动窗口内累计用量已达上限），请稍后再试或调整预算",
            budget,
        )
    try:
        report = save_diagnosis_report(
            session,
            org_id=org_id,
            system_id=system.id,
            user_id=_user_id_from_actor(actor_id),
            external_request_id=external_request_id,
            report_type="diagnose",
            question=question,
            business_context={**(business_context or {}), "budget_reservation_id": budget["reservation_id"]},
            answer="",
            status="running",
            commit=False,
        )
        if submission_id:
            from app.models.submissions import DiagnosisSubmission
            submission = session.get(DiagnosisSubmission, submission_id)
            if submission is None or submission.report_id is not None:
                raise RuntimeError("诊断提交身份不再有效")
            submission.report_id = report.id
            session.add(submission)
        from .jobs import add_job
        add_job(session, report, job_question if job_question is not None else question,
                {"actor_id": actor_id, **(job_options or {})})
        session.commit()
        session.refresh(report)
    except BaseException:
        session.rollback()
        raise
    return _build_diagnose_response(
        report_id=report.id,
        system_id=system.id,
        status="running",
        answer="排队中，等待诊断执行资源（最多等待 15 分钟）…",
    )


def start_resume(
    session: Session,
    source_report_id: int,
    system_id: int,
    org_id: int,
    *,
    actor_id: str = "",
    force_refresh: bool = False,
    job_options: dict | None = None,
) -> tuple[DiagnoseResponse, dict | None]:
    """继续一个被中断的诊断（**幂等**）。

    返回 (响应, 续跑载荷)。载荷为 None 表示"已经有人继续过"，本次不启动新执行。
    """
    from app.services.diagnostics.resume import (
        ResumeNotPossible,
        find_existing_resume,
        prepare_resume,
    )
    from app.services.diagnostics.usage import BudgetExceeded, release_budget, reserve_budget

    system = require_system(session, system_id, org_id)
    source = session.get(DiagnosisReport, source_report_id)
    if not source or source.org_id != org_id or source.system_id != system.id:
        raise LookupError("诊断报告不存在")

    if (source.business_context or {}).get("chat_id"):
        from .chats import attach_chat
        attach_chat(session, system_id, org_id, source.business_context["chat_id"])
    # 幂等：同一源报告只允许被继续一次
    existing = find_existing_resume(session, source.id)
    if existing is not None:
        return (
            _build_diagnose_response(
                report_id=existing.id,
                system_id=system.id,
                status=existing.status,
                answer=existing.answer or "该诊断已继续过，正在执行或已完成",
            ),
            None,
        )

    payload = prepare_resume(session, source, force_refresh=force_refresh)
    user_id = _user_id_from_actor(actor_id) or 0
    budget = reserve_budget(org_id=org_id, user_id=user_id, system_id=system.id, transaction=session)
    if not budget["allowed"]:
        raise BudgetExceeded("会话预算不足（滚动窗口内累计用量已达上限）", budget)

    from sqlalchemy.exc import IntegrityError

    try:
        report = save_diagnosis_report(
            session,
            org_id=org_id,
            system_id=system.id,
            user_id=user_id or None,
            report_type="diagnose",
            question=source.question or "（继续上次诊断）",
            status="running",
            answer="",
            business_context={**({"chat_id": source.business_context["chat_id"]} if (source.business_context or {}).get("chat_id") else {}), "interaction": (source.business_context or {}).get("interaction", {"requested":"auto","resolved":"diagnosis","reason":"legacy_resume","version":1}), "runtime_engine": payload["engine"], "budget_reservation_id": budget["reservation_id"], "resume": {"from_report_id": source.id, "force_refresh": force_refresh},
                              "images": (source.business_context or {}).get("images", [])},
            resumed_from_report_id=source.id,      # 唯一约束：并发/重复点击只有一个赢
            commit=False,
        )
        from .jobs import add_job
        add_job(session, report, "", {"actor_id": actor_id, **(job_options or {}), "resume_payload": payload, "interaction_kind": (source.business_context or {}).get("interaction",{}).get("resolved","diagnosis")})
        session.commit()
    except IntegrityError:
        # 并发点击：另一个请求刚插入成功 → 回滚预算、返回已存在的那条
        session.rollback()
        existing = find_existing_resume(session, source.id)
        if existing is None:
            raise
        return (
            _build_diagnose_response(
                report_id=existing.id, system_id=system.id, status=existing.status,
                answer=existing.answer or "该诊断已继续过，正在执行或已完成",
            ),
            None,
        )

    record_audit_event(
        session,
        org_id=org_id,
        system_id=system.id,
        actor_type="user",
        actor_id=actor_id,
        event_type="diagnosis.resume_started",
        target_type="diagnosis",
        target_id=str(report.id),
        input={"from_report_id": source.id, "force_refresh": force_refresh,
               "stale_evidence": len(payload.get("stale") or [])},
        commit=True,
    )
    return (
        _build_diagnose_response(
            report_id=report.id,
            system_id=system.id,
            status="running",
            answer="排队中，等待继续上次诊断…",
        ),
        payload,
    )


def _finalize_report(
    report_id: int,
    org_id: int,
    system_id: int,
    *,
    status: str,
    result: DiagnoseResponse | None = None,
    error: str = "",
    cancel_phase: str = "",
    total_tokens_override: int | None = None,
) -> bool:
    """**原子条件**写终态：`UPDATE … WHERE status='running'`，返回是否本次写入成功。

    口径：`total_tokens` 表示**本次运行自身**的消耗（resume 时框架给的是含前次的累计值，
    不能直接落库，否则报告视图会把上一段诊断重复算进来）。会话累计由用量账本 SUM(delta) 提供。

    旧实现是"读 → 判断 status == running → 写"，两个写者（比如运行方落 success 与
    关停路径落 cancelled 同时发生）可能都读到 running 然后互相覆盖。现在把条件放进
    UPDATE 的 WHERE 里，由数据库保证**只有一个**写者成功（先到先赢）。
    """
    from sqlalchemy import func, update
    from app.services.diagnostics import cancel_bus

    from app.core.database import engine

    values: dict = {"status": status}
    if status == "success" and result is not None:
        values.update(
            {
                "answer": result.answer,
                "template_name": result.template_name,
                "template_description": result.template_description,
                "model": result.model,
                "duration_ms": result.duration_ms,
                "total_tokens": (
                    result.total_tokens if total_tokens_override is None else total_tokens_override
                ),
                "evidence_sources": result.evidence_sources,
                "evidence_steps": result.evidence_steps,
                "tool_calls": result.tool_calls,
                "evidence": [item.model_dump() for item in result.evidence],
                "knowledge_refs": [item.model_dump() for item in result.knowledge_refs],
            }
        )
    elif status == "cancelled":
        values.update(
            {
                "cancelled_at": datetime.now(timezone.utc),
                "cancel_phase": cancel_phase or "",
                # 仅在 answer 为空时写占位文案（不覆盖已有的部分内容）
                "answer": func.coalesce(func.nullif(DiagnosisReport.answer, ""), "（诊断已被中断）"),
            }
        )
        if result is not None:
            values.update(
                {
                    "model": result.model,
                    "duration_ms": result.duration_ms,
                    "total_tokens": (
                        result.total_tokens if total_tokens_override is None else total_tokens_override
                    ),
                    "tool_calls": result.tool_calls,
                }
            )
    else:
        if "账本" in error:
            values.update(usage_complete=False,usage_gap_reason="accounting_unavailable")
        values.update(
            {
                "error_message": error,
                "answer": func.coalesce(
                    func.nullif(DiagnosisReport.answer, ""), f"诊断失败：{error}"
                ),
            }
        )

    from app.agent.harness.performance import current as current_performance
    performance=current_performance()
    if performance:
        for event in list(performance.events):
            if event['stage'] in ('context','report') and event['duration_ms'] is None:
                performance.end(event['id'], 'success' if status=='success' else status)
        performance.finish(status=status)
    with Session(engine) as session:
        stmt = (
            update(DiagnosisReport)
            .where(
                DiagnosisReport.id == report_id,
                DiagnosisReport.org_id == org_id,
                DiagnosisReport.system_id == system_id,
                DiagnosisReport.status == "running",   # ← 原子条件：先到先赢
                DiagnosisReport.runner_id == cancel_bus.INSTANCE_ID,
            )
            .values(**values)
        )
        rowcount = session.execute(stmt).rowcount
        if rowcount:
            report=session.get(DiagnosisReport,report_id)
            record_audit_event(session,org_id=org_id,system_id=system_id,
                event_type={'success':'diagnosis.completed','cancelled':'diagnosis.cancelled'}.get(status,'diagnosis.failed'),
                actor_type='platform',actor_id=str(report.user_id or ''),
                target_type='diagnosis',target_id=str(report_id),status=status,
                output={'status':status,'error':error,'cancel_phase':cancel_phase})
        session.commit()
    return bool(rowcount)


def complete_diagnosis_report(
    report_id: int,
    system_id: int,
    org_id: int,
    question: str,
    *,
    actor_id: str = "",
    model_mode: str = "auto",
    model_name: str = "",
    follow_up_report_id: int | None = None,
    images: list[tuple[str, bytes]] | None = None,
    resume_payload: dict | None = None,
    runtime_engine: str = "pydantic",
    interaction_kind: str = "diagnosis",
    chat_context: list | None = None,
) -> None:
    from app.core.database import engine
    from app.services.diagnostics import cancel_bus
    from app.services.diagnostics.run_state import save_run_state

    # 本轮的原生取消令牌：单次使用（一旦取消就保持取消，传入已取消的 token 会让 run 不启动，
    # 正好堵住"取消早于启动"的竞态）。注册后立即自检 DB 标志，并启动兜底轮询。
    token = get_runtime((resume_payload or {}).get("engine", runtime_engine)).create_cancellation()
    registration = cancel_bus.register(report_id, token,org_id=org_id,system_id=system_id)
    if registration is None:
        log.info('[diagnose] 未取得运行所有权，不重复启动 report=%s',report_id)
        return
    cancel_bus.start_poller(report_id, registration)
    progress = _progress_recorder(report_id, org_id, system_id)
    from app.agent.harness.performance import current as current_performance
    performance=current_performance()
    if performance:
        original_progress=progress
        def observed_progress(payload):
            if payload.get('kind')=='answer_delta' and payload.get('text'): performance.text()
            if payload.get('kind')=='answer_attempt':
                ident=performance.begin('attempt',name='fallback');performance.end(ident)
            original_progress(payload)
        observed_progress.close=original_progress.close
        progress=observed_progress

    try:
        with Session(engine) as session:
            report = session.get(DiagnosisReport, report_id)
            if not report or report.org_id != org_id or report.system_id != system_id:
                return
            # 在会话内取出「用户原始问题」：会话关闭后 report 是 detached 实例，
            # 再读属性会抛 DetachedInstanceError（旧版正是在会话外读 report.question，
            # 导致内部诊断的记忆写入一直静默失败）。
            original_question = report.question or question
            try:
                result = diagnose_system(
                    session,
                    system_id,
                    org_id,
                    question,
                    actor_id=actor_id,
                    existing_report_id=report_id,
                    model_mode=model_mode,
                    model_name=model_name,
                    interaction_kind=interaction_kind, chat_context=chat_context,
                    on_progress=progress,
                    follow_up_report_id=follow_up_report_id,
                    intent_text=original_question,
                    images=images,
                    cancel_token=token,
                    on_phase=lambda phase: cancel_bus.set_phase(report_id, phase),
                    message_history=_resume_history(resume_payload),
                    prior_usage=_resume_prior_usage(resume_payload),
                    prompt_override=(resume_payload or {}).get("note"),
                    runtime_engine=(resume_payload or {}).get("engine", runtime_engine),
                    runtime_engine_version=(resume_payload or {}).get("engine_version", ""),
                    history_schema_version=(resume_payload or {}).get("history_schema_version", 1),
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("[diagnose] 诊断执行失败 report_id=%s", report_id)
                progress.close()
                partial = getattr(exc, "partial_result", None)
                if partial is not None:
                    _record_usage_and_settle(report_id=report_id, org_id=org_id, system_id=system_id,
                        actor_id=actor_id, result=partial, cancelled=bool(partial.cancelled), cancel_phase=cancel_bus.current_phase(report_id),
                        run_kind="resume" if resume_payload else "initial", baseline_json=(resume_payload or {}).get("baseline_json") or "")
                    save_run_state(report_id, messages_json=partial.messages_json, usage_json=partial.usage_json,
                        run_id=partial.run_id, conversation_id=partial.conversation_id, engine=partial.engine,
                        engine_version=partial.engine_version, history_schema_version=partial.history_schema_version, resumable=partial.resume_safe)
                try:
                    _finalize_report(report_id, org_id, system_id, status="failed", error=str(exc))
                except Exception:  # noqa: BLE001
                    log.exception("[diagnose] 写入失败状态也失败 report_id=%s", report_id)
                # 自改进：失败案例进候选队列（人工审核后才入库，失败静默）
                from app.services.improvements import capture_failure

                capture_failure(report_id, str(exc))
                return

        progress.close()

        # ── 记账：必须在覆盖 run_state 的"框架累计"之前算增量 ──────────────
        try:
            usage_summary = _record_usage_and_settle(
                report_id=report_id,
                org_id=org_id,
                system_id=system_id,
                actor_id=actor_id,
                result=result,
                cancelled=bool(getattr(result, "cancelled", False)),
                cancel_phase=cancel_bus.current_phase(report_id),
                run_kind="resume" if resume_payload else "initial",
                baseline_json=(resume_payload or {}).get("baseline_json") or "",
            )
        except RuntimeExecutionError as exc:
            save_run_state(report_id,messages_json=result.messages_json,usage_json=result.usage_json,
                conversation_id=result.conversation_id,run_id=result.run_id,engine=result.engine,
                engine_version=result.engine_version,history_schema_version=result.history_schema_version,resumable=result.resume_safe)
            _finalize_report(report_id,org_id,system_id,status="failed",error=str(exc))
            return


        # 中断：原生 RunCancelled 已转成可恢复结果 → 落 cancelled + 保存完整消息历史，且**不写记忆**
        if getattr(result, "cancelled", False):
            # 顺序很重要：**先落历史，再落终态**。客户端看到 status=cancelled 就会停止轮询，
            # 若历史还没落库，就会把"可继续"误显示为"未取得可恢复历史"。
            save_run_state(
                report_id,
                messages_json=getattr(result, "messages_json", "") or "",
                conversation_id=getattr(result, "conversation_id", "") or "",
                run_id=getattr(result, "run_id", "") or "",
                usage_json=getattr(result, "usage_json", "") or "",
                resumable=bool(getattr(result, "messages_json", "")) and getattr(result, "resume_safe", True),
                engine=getattr(result, "engine", "pydantic"),
                engine_version=getattr(result, "engine_version", ""),
                history_schema_version=getattr(result, "history_schema_version", 1),
            )
            try:
                _finalize_report(
                    report_id,
                    org_id,
                    system_id,
                    status="cancelled",
                    result=result,
                    cancel_phase=cancel_bus.current_phase(report_id),
                    total_tokens_override=usage_summary.get("tokens_delta"),
                )
            except Exception:  # noqa: BLE001
                log.exception("[diagnose] 写入取消状态失败 report_id=%s", report_id)
            return

        # 可恢复历史：先落库，再落终态（保证"看到终态时，可恢复性已确定"）
        save_run_state(
            report_id,
            messages_json=getattr(result, "messages_json", "") or "",
            conversation_id=getattr(result, "conversation_id", "") or "",
            run_id=getattr(result, "run_id", "") or "",
            usage_json=getattr(result, "usage_json", "") or "",
            resumable=bool(getattr(result, "messages_json", "")) and getattr(result, "resume_safe", True),
            engine=getattr(result, "engine", "pydantic"),
            engine_version=getattr(result, "engine_version", ""),
            history_schema_version=getattr(result, "history_schema_version", 1),
        )
        try:
            _finalize_report(
                report_id, org_id, system_id, status="success", result=result,
                total_tokens_override=usage_summary.get("tokens_delta"),
            )
        except Exception:  # noqa: BLE001
            log.exception("[diagnose] 写入成功状态失败 report_id=%s", report_id)
    finally:
        # 结束时注销注册表并放行 poller（done 事件），避免线程与内存泄漏
        progress.close()
        cancel_bus.unregister(report_id)
    # 结构化记忆：诊断成功后沉淀经验（污染受控：置信度 0.5、待回查验证）
    from app.core.config import settings as app_settings

    if app_settings.diagnosis_memory_enabled and result and interaction_kind != "chat":
        from app.services.knowledge.memory import save_memory

        # 记忆的 symptom 必须是「用户原始问题」：外部链路会把只读快照拼进
        # 传给模型的 question，若直接落库会把大段上下文写进记忆（污染检索）。
        # original_question 在会话内取自 report.question（start_diagnosis 时保存的原始问题）。
        save_memory(
            org_id=org_id,
            system_id=system_id,
            question=original_question,
            answer=result.answer,
            report_id=report_id,
        )


def _resume_history(payload: dict | None):
    if not payload:
        return None
    from app.services.diagnostics.resume import load_history_for_run

    return load_history_for_run(payload)


def _resume_prior_usage(payload: dict | None):
    return (payload or {}).get("baseline_json") if payload else None


def _clock_hint() -> str:
    """当前时间提示：**易变内容**，单独放在系统提示末尾（原先混在数据集目录里，
    每天变化会把前缀缓存打散）。"""
    now = datetime.now(timezone.utc).astimezone()
    weekday = "一二三四五六日"[now.weekday()]
    return f"## 当前时间\n（当前时间：{now:%Y-%m-%d %H:%M}，周{weekday}）"


def _record_usage_and_settle(
    *,
    report_id: int,
    org_id: int,
    system_id: int,
    actor_id: str,
    result,
    cancelled: bool,
    cancel_phase: str,
    run_kind: str = "initial",
    baseline_json: str = "",
) -> dict:
    """Commit the attempt ledger and original-window budget in one transaction."""
    import time
    from app.services.diagnostics.session import session as db_session
    from app.services.diagnostics.usage import record_run_usage, settle_budget
    from app.models.run_state import DiagnosisRunState
    from app.agent.harness.performance import current as current_performance
    performance=current_performance()
    if performance:
        try:
            usage=json.loads(getattr(result,'usage_json','') or '{}')
            with performance.lock:
                for kind,key in (('request','reported_requests'),('tool','reported_tool_calls')):
                    performance.budgets.setdefault(kind,{})['framework_reported']=usage.get(key)
        except Exception: performance.gap('framework_usage_missing')
    for attempt in range(3):
        try:
            with db_session() as db:
                report=db.get(DiagnosisReport,report_id)
                if report is None:
                    raise LookupError("诊断不存在")
                state=db.get(DiagnosisRunState,report_id)
                baseline=baseline_json
                summary=record_run_usage(report_id=report_id,org_id=org_id,system_id=system_id,
                    conversation_id=getattr(result,"conversation_id","") or (state.conversation_id if state else ""),
                    run_kind=run_kind,usage_json=getattr(result,"usage_json","") or "",cancelled=cancelled,
                    cancel_phase=cancel_phase,before_json=baseline,session=db,commit=False)
                reservation_id=(report.business_context or {}).get("budget_reservation_id","")
                if reservation_id and not summary.get("usage_complete"):
                    from app.models.usage import DiagnosisBudgetReservation
                    reservation=db.get(DiagnosisBudgetReservation,reservation_id)
                    if reservation is None:
                        raise ValueError("预算预留缺失")
                    summary["settle_tokens"]=max(summary.get("tokens_delta",0),reservation.reserved_tokens)
                    summary["settle_cost"]=max(summary.get("cost_delta",0),reservation.reserved_cost)
                if reservation_id or not summary.get("duplicate"):
                    settle_budget(org_id=org_id,user_id=_user_id_from_actor(actor_id) or 0,system_id=system_id,
                        tokens=summary.get("settle_tokens",0),cost_usd=summary.get("settle_cost",summary.get("cost_delta",0.0)),
                        reservation_id=reservation_id,moment=report.created_at,session=db if reservation_id else None)
                db.commit()
                return summary
        except Exception:
            log.exception("[diagnose] 账本事务失败 report=%s attempt=%s",report_id,attempt+1)
            if attempt<2:
                time.sleep(.1*(attempt+1))
    # Do not claim zero or success after an accounting failure. Reservation stays held.
    raise RuntimeExecutionError("用量账本/预算未能落库；预留保留，需要重试结算")


def diagnose_system(
    session: Session,
    system_id: int,
    org_id: int,
    question: str,
    *,
    actor_id: str = "",
    existing_report_id: int | None = None,
    model_mode: str = "auto",
    model_name: str = "",
    follow_up_report_id: int | None = None,
    on_progress=None,
    intent_text: str = "",
    images: list[tuple[str, bytes]] | None = None,
    cancel_token=None,
    on_phase=None,
    message_history=None,
    prior_usage=None,
    prompt_override: str | None = None,
    clock_hint: str = "",
    runtime_engine: str = "pydantic",
    runtime_engine_version: str = "",
    history_schema_version: int = 1,
    interaction_kind: str = "diagnosis",
    chat_context: list | None = None,
) -> DiagnoseResponse:
    system = require_system(session, system_id, org_id)
    if interaction_kind=='chat':
        return _run_chat(system,question,org_id=org_id,existing_report_id=existing_report_id,
            model_mode=model_mode,model_name=model_name,on_progress=on_progress,on_phase=on_phase,
            cancel_token=cancel_token,message_history=message_history,prior_usage=prior_usage,
            prompt_override=prompt_override,runtime_engine=runtime_engine,
            runtime_engine_version=runtime_engine_version,history_schema_version=history_schema_version,
            chat_context=chat_context)
    started_at = time.monotonic()
    user_id = _user_id_from_actor(actor_id)
    from app.agent.harness.request_profile import is_availability_question, AVAILABILITY_STEPS

    intent = intent_text or question
    availability_only = is_availability_question(intent) and not images and not follow_up_report_id and not message_history
    template = match_skill(intent, _disabled_template_names(system))
    if availability_only and template and template["name"] == "health_check":
        template = {**template, "steps": AVAILABILITY_STEPS}
    else:
        availability_only = False
    descriptor = system_to_descriptor(system, list_enabled_services_for_system(session, system.id))
    # Current availability comes from live probes. Knowledge remains available
    # through tools; detailed, historical and follow-up requests retain retrieval.
    knowledge_context = "" if availability_only else get_relevant_context(intent, str(system.id))
    clock_hint = _clock_hint()
    conversation_context = ""
    if follow_up_report_id:
        prev_report = session.get(DiagnosisReport, follow_up_report_id)
        if prev_report and prev_report.org_id == org_id and prev_report.system_id == system.id:
            prev_answer = (prev_report.answer or "")[:1200]
            conversation_context = (
                f"上一轮问题: {(prev_report.question or '')[:300]}\n"
                f"上一轮结论: {prev_answer}"
            )
    dataset_query, data_catalog = _business_dataset_query(session, system, org_id, actor_id)
    archive_list, archive_read, archive_fetch, archive_raw = _archive_access(session, system, org_id)
    run_code = _code_sandbox(system, archive_raw)

    from app.agent.diagnostics.knowledge.runbooks import describe_catalog as _runbook_catalog_text
    from app.agent.diagnostics.knowledge.runbooks import load_runbook as _load_runbook_body

    runbook_catalog = _runbook_catalog_text(system.id)

    def _load_runbook(name: str) -> str:
        return _load_runbook_body(system.id, name) or ""

    from app.services.topology import build_graph as _build_topology
    from app.services.topology import describe as _describe_topology
    from app.services.topology import neighbors as _topology_neighbors

    topology = _build_topology(system, list_enabled_services_for_system(session, system.id))
    topology_catalog = _describe_topology(topology)

    def _topology_query(service: str = "") -> str:
        if not topology.get("edges"):
            return "当前系统没有配置服务依赖拓扑。"
        name = str(service or "").strip()
        return _topology_neighbors(topology, name) if name else _describe_topology(topology)

    from app.services.changes import list_changes as _list_changes
    from app.services.changes import recent_changes_text as _changes_text

    changes_catalog = _changes_text(session, system.id, org_id, hours=24)

    def _changes_query(service: str = "") -> str:
        # 工具可能被线程池并发调用，用独立 Session，别复用诊断主 Session
        from app.core.database import engine

        try:
            with Session(engine) as scoped:
                rows = _list_changes(scoped, system.id, org_id, hours=72, limit=20)
        except Exception:  # noqa: BLE001
            return "查询变更记录失败。"
        name = str(service or "").strip()
        if name:
            rows = [row for row in rows if row.service == name or not row.service]
        if not rows:
            return "最近 72 小时内没有变更记录。" + (f"（服务：{name}）" if name else "")
        lines = []
        for row in rows[:15]:
            ts = row.occurred_at.strftime("%Y-%m-%d %H:%M UTC") if row.occurred_at else "-"
            target = f" @{row.service}" if row.service else ""
            lines.append(f"- {ts}｜{row.kind}{target}：{row.title}（{row.actor or '未知'}）")
        return "最近变更（近 72 小时）：\n" + "\n".join(lines)

    from app.services.topology import failure_context as _failure_context

    health_remote = _remote_command(session, system)

    def _live_failing() -> list[str] | None:
        """与 agent 的 list_services 同源：优先经采集器实时探活，否则本地直接探测。"""
        try:
            remote = health_remote
            if remote:
                resp = remote("health_check", {}) or {}
                if not resp.get("ok"):
                    return None
                items = resp.get("result")
            else:
                from app.services.descriptors.health import collect_health

                items = collect_health(descriptor)
            if not items:
                return None
            return [i.get("name") for i in (items or []) if isinstance(i, dict) and not i.get("ok")]
        except Exception:  # noqa: BLE001
            return None

    # Defer this extra probe until failure analysis is actually requested.
    # list_services remains the normal audited source of current availability.
    failure_catalog = ""

    from app.agent.diagnostics import mcp as mcp_client

    mcp_catalog = mcp_client.catalog(org_id=org_id, system_id=system.id)

    def _failure_query() -> str:
        live_failing = _live_failing()
        if live_failing is None:
            return "实时探活未取得有效结果，不能判断当前是否有异常服务。"
        return (
            _failure_context(topology, live_failing)
            or "当前没有探测到异常服务，或未配置服务依赖拓扑。"
        )

    from app.agent.harness.performance import current as current_performance
    performance=current_performance()
    if performance:
        for event in list(performance.events):
            if event['stage']=='context' and event['duration_ms'] is None: performance.end(event['id'])
    try:
        run = diagnose_with_details(
            descriptor,
            question,
            org_id=org_id,
            system_id=system.id,
            skill_steps=template["steps"] if template else "",
            knowledge_context=knowledge_context,
            conversation_context=conversation_context,
            remote_command=health_remote,
            business_data_query=_business_data_query(session, system, org_id, actor_id),
            business_dataset_query=dataset_query,
            archive_list=archive_list,
            archive_read=archive_read,
            archive_fetch=archive_fetch,
            run_code=run_code,
            load_runbook=_load_runbook,
            data_catalog=data_catalog,
            runbook_catalog=runbook_catalog,
            topology_catalog=topology_catalog,
            topology_query=_topology_query,
            changes_catalog=changes_catalog,
            changes_query=_changes_query,
            failure_catalog=failure_catalog,
            failure_query=_failure_query,
            mcp_catalog=mcp_catalog,
            intent_text=intent_text,
            model_mode=model_mode,
            model_name=model_name,
            on_progress=on_progress,
            tool_audit_sink=_tool_audit_recorder(org_id, system.id, actor_id, existing_report_id),
            on_runtime_event=(lambda event: _persist_runtime_event(event, org_id, system.id)) if runtime_engine == "pi" else None,
            runtime_run_id=str(existing_report_id or ""),
            images=images,
            clock_hint=clock_hint,
            cancel_token=cancel_token,
            on_phase=on_phase,
            message_history=message_history,
            prior_usage=prior_usage,
            prompt_override=prompt_override,
            runtime_engine=runtime_engine,
            runtime_engine_version=runtime_engine_version,
            history_schema_version=history_schema_version,
        )
    except (RuntimeCompatibilityError, ToolAuditError, RuntimeBudgetExceeded, RuntimeExecutionError):
        raise  # compatibility, audit, and budget failures must never restart through fallback
    except Exception as exc:
        if _is_kafka_lag_question(question):
            try:
                result = _kafka_lag_fallback(
                    session,
                    system,
                    org_id,
                    question,
                    started_at=started_at,
                    user_id=user_id,
                    existing_report_id=existing_report_id,
                )
                record_audit_event(
                    session,
                    org_id=org_id,
                    system_id=system.id,
                    event_type="diagnosis.result_ready" if existing_report_id else "diagnosis.completed",
                    actor_type="user",
                    actor_id=actor_id,
                    target_type="diagnosis",
                    target_id=str(result.id or ""),
                    status="success",
                    input={
                        "question": question,
                        "fallback": "kafka_metrics",
                        "model_error": str(exc),
                    },
                    output={
                        "answer": result.answer,
                        "model": result.model,
                        "duration_ms": result.duration_ms,
                        "evidence_sources": result.evidence_sources,
                    },
                    commit=True,
                )
                return result
            except Exception:
                pass
        record_audit_event(
            session,
            org_id=org_id,
            system_id=system.id,
            event_type="diagnosis.result_failed" if existing_report_id else "diagnosis.failed",
            actor_type="user",
            actor_id=actor_id,
            target_type="diagnosis",
            status="failed",
            input={
                "question": question,
                "template_name": template["name"] if template else "",
                "model_mode": model_mode,
                "model_name": model_name,
            },
            output={
                "error": str(exc),
                "duration_ms": round((time.monotonic() - started_at) * 1000),
            },
            commit=True,
        )
        if not existing_report_id:
            save_diagnosis_report(
                session,
                org_id=org_id,
                system_id=system.id,
                user_id=user_id,
                report_type="diagnose",
                question=question,
                answer="",
                status="failed",
                template_name=template["name"] if template else "",
                template_description=template["description"] if template else "自由诊断",
                duration_ms=round((time.monotonic() - started_at) * 1000),
                error_message=str(exc),
            )
        raise

    if performance: performance.begin('report')
    from app.agent.harness.evidence import apply_evidence_guard
    from app.core.config import settings as app_settings

    from .verification import verification_calls as scoped_verification_calls
    verification_calls = scoped_verification_calls(session, existing_report_id, org_id, system.id, run.tool_calls)
    answer_text, guard_meta = apply_evidence_guard(
        run.answer,
        verification_calls,
        enabled=app_settings.diagnosis_evidence_guard,
    )

    if availability_only:
        from app.agent.harness.evidence import evidence_available

        health_calls = [call for call in run.tool_calls if call.get("tool") == "list_services"]
        if not evidence_available(health_calls) and not getattr(run, "cancelled", False):
            guard_meta.update(flagged=True, reason="live_health_evidence_missing")

    evidence_sources = _evidence_sources(template["steps"] if template else "", run.tool_calls)
    evidence_items = build_evidence_from_tool_calls(run.tool_calls)
    evidence_steps = build_evidence_steps(run.tool_calls)
    knowledge_refs = build_knowledge_refs(system.id, run.tool_calls, question="" if availability_only else intent)
    total_duration_ms = round((time.monotonic() - started_at) * 1000)
    log.info("[diagnosis-timing] report=%s total_ms=%s engine_ms=%s availability_only=%s",
             existing_report_id, total_duration_ms, run.duration_ms, availability_only)
    record_audit_event(
        session,
        org_id=org_id,
        system_id=system.id,
        event_type="diagnosis.result_ready" if existing_report_id else "diagnosis.completed",
        actor_type="user",
        actor_id=actor_id,
        target_type="diagnosis",
        target_id=str(existing_report_id or ""),
        status="success",
        input={
            "question": question,
            "template_name": template["name"] if template else "",
            "template_description": template["description"] if template else "自由诊断",
            "model_mode": model_mode,
            "model_name": model_name,
        },
        output={
            "answer": answer_text,
            "model": run.model,
            "duration_ms": total_duration_ms,
            "total_tokens": run.total_tokens,
            "evidence_sources": evidence_sources,
            "tool_calls": run.tool_calls,
            "evidence_guard": guard_meta,
        },
        commit=False,
    )
    if existing_report_id:
        report = session.get(DiagnosisReport, existing_report_id)
        if not report:
            raise LookupError("诊断报告不存在")
        session.refresh(report)
        from sqlalchemy import update
        session.execute(update(DiagnosisReport).where(
            DiagnosisReport.id == report.id, DiagnosisReport.org_id == org_id,
            DiagnosisReport.system_id == system.id, DiagnosisReport.status == 'running',
            DiagnosisReport.cancel_requested_at == report.cancel_requested_at,
        ).values(business_context={**(report.business_context or {}), 'evidence_validation': guard_meta}))
        # Result-stage evidence/audit must survive this Session closing. The
        # authoritative completed/failed event is written with terminal state.
        session.commit()
    else:
        report = save_diagnosis_report(
            session,
            org_id=org_id,
            system_id=system.id,
            user_id=user_id,
            report_type="diagnose",
            question=question,
            answer=answer_text,
            template_name=template["name"] if template else "",
            template_description=template["description"] if template else "自由诊断",
            model=run.model,
            duration_ms=total_duration_ms,
            total_tokens=run.total_tokens,
            evidence_sources=evidence_sources,
            evidence_steps=evidence_steps,
            tool_calls=run.tool_calls,
            evidence=evidence_items,
            knowledge_refs=knowledge_refs,
            business_context={"evidence_validation": guard_meta},
            commit=True,
        )
    return _build_diagnose_response(
        report_id=report.id,
        system_id=system.id,
        answer=answer_text,
        template_name=template["name"] if template else "",
        template_description=template["description"] if template else "自由诊断",
        model=run.model,
        duration_ms=total_duration_ms,
        total_tokens=run.total_tokens,
        evidence_sources=evidence_sources,
        tool_calls=run.tool_calls,
        evidence=evidence_items,
        evidence_steps=evidence_steps,
        knowledge_refs=knowledge_refs,
        cancelled=getattr(run, "cancelled", False),
        messages_json=getattr(run, "messages_json", "") or "",
        conversation_id=getattr(run, "conversation_id", "") or "",
        run_id=getattr(run, "run_id", "") or "",
        usage_json=getattr(run, "usage_json", "") or "",
        resume_safe=getattr(run, "resume_safe", True),
        engine=getattr(run, "engine", "pydantic"),
        engine_version=getattr(run, "engine_version", ""),
        history_schema_version=getattr(run, "history_schema_version", 1),
        business_context={"evidence_validation": guard_meta},
    )


def _tool_audit_recorder(org_id, system_id, actor_id, report_id):
    """Own session per record: no live request Session shared with tool threads."""
    from app.core.database import engine

    def record(payload):
        with Session(engine) as audit_session:
            record_audit_event(
                audit_session, org_id=org_id, system_id=system_id,
                actor_type="agent", actor_id=actor_id, event_type="agent.tool",
                target_type="diagnosis_report", target_id=str(report_id or ""),
                status=payload["status"],
                input={"tool": payload["tool"], "call_id": payload["call_id"],
                       "arguments": payload["input"], "scope": payload["scope"]},
                output={key: payload.get(key) for key in ("error_code", "duration_ms")},
                commit=True,
            )
    return record


def _persist_runtime_event(event, org_id, system_id):
    from app.services.diagnostics.runtime_events import persist_event
    persist_event(event, org_id, system_id)


def _run_chat(system,question,*,org_id,existing_report_id,chat_context=None,**options):
    """Only skip diagnostic preparation; all lifecycle/settlement stays in the caller."""
    from app.agent.harness.performance import current
    performance=current()
    if performance:
        for event in list(performance.events):
            if event['stage']=='context' and event['duration_ms'] is None:performance.end(event['id'])
    options['on_runtime_event']=(lambda event:_persist_runtime_event(event,org_id,system.id)) if options.get('runtime_engine')=='pi' else None
    run=diagnose_with_details({},question,org_id=org_id,system_id=system.id,
        interaction_kind='chat',chat_context=chat_context or [],runtime_run_id=str(existing_report_id or ''),**options)
    if performance:performance.begin('report')
    return _build_diagnose_response(report_id=existing_report_id,system_id=system.id,answer=run.answer,
        template_description='日常聊天（未查询实时系统数据）',model=run.model,duration_ms=run.duration_ms,
        total_tokens=run.total_tokens,tool_calls=[],evidence=[],evidence_sources=[],evidence_steps=[],knowledge_refs=[],
        cancelled=run.cancelled,messages_json=run.messages_json,conversation_id=run.conversation_id,
        run_id=run.run_id,usage_json=run.usage_json,resume_safe=run.resume_safe,engine=run.engine,
        engine_version=run.engine_version,history_schema_version=run.history_schema_version)

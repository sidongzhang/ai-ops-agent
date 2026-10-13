"""Application services for AI diagnosis and diagnostic playbooks."""
import json
import logging
import time

from sqlmodel import Session

from app.agent.diagnostics.runner import diagnose_with_details
from app.agent.diagnostics.skill_router import match_skill
from app.agent.diagnostics.knowledge.store import get_relevant_context_with_memories as get_relevant_context
from app.repositories.systems import list_enabled_services_for_system
from app.services.collectors.exec import select_online_collector
from app.schemas import DiagnoseResponse
from app.services.audit import record_audit_event
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

    The frontend already polls this report, so writing here gives a live tool
    chain without adding a new streaming channel. Failures are swallowed on
    purpose: progress reporting must never break a diagnosis.
    """
    from app.core.database import engine

    def record(event: dict) -> None:
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
) -> DiagnoseResponse:
    system = require_system(session, system_id, org_id)
    report = save_diagnosis_report(
        session,
        org_id=org_id,
        system_id=system.id,
        user_id=_user_id_from_actor(actor_id),
        external_request_id=external_request_id,
        report_type="diagnose",
        question=question,
        business_context=business_context,
        answer="",
        status="running",
        commit=True,
    )
    return _build_diagnose_response(
        report_id=report.id,
        system_id=system.id,
        status="running",
        answer="诊断进行中，正在收集健康检查、日志和指标证据…",
    )


def _finalize_report(
    report_id: int,
    org_id: int,
    system_id: int,
    *,
    status: str,
    result: DiagnoseResponse | None = None,
    error: str = "",
) -> None:
    """Write the terminal state with a brand new Session.

    The diagnosis Session can end up in a poisoned state (parallel tool calls,
    lock contention). Reusing it here is how a report gets stuck at `running`
    forever, so the terminal write always gets a clean Session of its own.
    """
    from app.core.database import engine

    with Session(engine) as session:
        pending = session.get(DiagnosisReport, report_id)
        if not pending or pending.org_id != org_id or pending.system_id != system_id:
            return
        if pending.status != "running":
            return
        if status == "success" and result is not None:
            pending.status = "success"
            pending.answer = result.answer
            pending.template_name = result.template_name
            pending.template_description = result.template_description
            pending.model = result.model
            pending.duration_ms = result.duration_ms
            pending.total_tokens = result.total_tokens
            pending.evidence_sources = result.evidence_sources
            pending.evidence_steps = result.evidence_steps
            pending.tool_calls = result.tool_calls
            pending.evidence = [item.model_dump() for item in result.evidence]
            pending.knowledge_refs = [item.model_dump() for item in result.knowledge_refs]
        else:
            pending.status = "failed"
            pending.error_message = error
            pending.answer = pending.answer or f"诊断失败：{error}"
        session.add(pending)
        session.commit()


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
) -> None:
    from app.core.database import engine

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
                on_progress=_progress_recorder(report_id, org_id, system_id),
                follow_up_report_id=follow_up_report_id,
                intent_text=original_question,
                images=images,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("[diagnose] 诊断执行失败 report_id=%s", report_id)
            try:
                _finalize_report(report_id, org_id, system_id, status="failed", error=str(exc))
            except Exception:  # noqa: BLE001
                log.exception("[diagnose] 写入失败状态也失败 report_id=%s", report_id)
            # 自改进：失败案例进候选队列（人工审核后才入库，失败静默）
            from app.services.improvements import capture_failure

            capture_failure(report_id, str(exc))
            return
    try:
        _finalize_report(report_id, org_id, system_id, status="success", result=result)
    except Exception:  # noqa: BLE001
        log.exception("[diagnose] 写入成功状态失败 report_id=%s", report_id)
    # 结构化记忆：诊断成功后沉淀经验（污染受控：置信度 0.5、待回查验证）
    from app.core.config import settings as app_settings

    if app_settings.diagnosis_memory_enabled and result:
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
) -> DiagnoseResponse:
    system = require_system(session, system_id, org_id)
    started_at = time.monotonic()
    user_id = _user_id_from_actor(actor_id)
    template = match_skill(question, _disabled_template_names(system))
    descriptor = system_to_descriptor(system, list_enabled_services_for_system(session, system.id))
    knowledge_context = get_relevant_context(question, str(system.id))
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

    def _live_failing() -> list[str]:
        """与 agent 的 list_services 同源：优先经采集器实时探活，否则本地直接探测。"""
        try:
            remote = _remote_command(session, system)
            if remote:
                resp = remote("health_check", {}) or {}
                items = resp.get("result") if resp.get("ok") else []
            else:
                from app.services.descriptors.health import collect_health

                items = collect_health(
                    system_to_descriptor(system, list_enabled_services_for_system(session, system.id))
                )
            return [i.get("name") for i in (items or []) if isinstance(i, dict) and not i.get("ok")]
        except Exception:  # noqa: BLE001
            return []

    live_failing = _live_failing()
    failure_catalog = _failure_context(topology, live_failing)

    from app.agent.diagnostics import mcp as mcp_client

    mcp_catalog = mcp_client.catalog()

    def _failure_query() -> str:
        return (
            _failure_context(topology, live_failing)
            or "当前没有探测到异常服务，或未配置服务依赖拓扑。"
        )

    try:
        run = diagnose_with_details(
            descriptor,
            question,
            org_id=org_id,
            system_id=system.id,
            skill_steps=template["steps"] if template else "",
            knowledge_context=knowledge_context,
            conversation_context=conversation_context,
            remote_command=_remote_command(session, system),
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
            images=images,
        )
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
                    event_type="diagnosis.completed",
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
            event_type="diagnosis.failed",
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

    from app.agent.diagnostics.evidence_guard import apply_evidence_guard
    from app.core.config import settings as app_settings

    answer_text, guard_meta = apply_evidence_guard(
        run.answer,
        run.tool_calls,
        enabled=app_settings.diagnosis_evidence_guard,
    )

    evidence_sources = _evidence_sources(template["steps"] if template else "", run.tool_calls)
    evidence_items = build_evidence_from_tool_calls(run.tool_calls)
    evidence_steps = build_evidence_steps(run.tool_calls)
    knowledge_refs = build_knowledge_refs(system.id, run.tool_calls, question=question)
    record_audit_event(
        session,
        org_id=org_id,
        system_id=system.id,
        event_type="diagnosis.completed",
        actor_type="user",
        actor_id=actor_id,
        target_type="diagnosis",
        target_id="",
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
            "duration_ms": run.duration_ms,
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
            duration_ms=run.duration_ms,
            total_tokens=run.total_tokens,
            evidence_sources=evidence_sources,
            evidence_steps=evidence_steps,
            tool_calls=run.tool_calls,
            evidence=evidence_items,
            knowledge_refs=knowledge_refs,
            commit=True,
        )
    return _build_diagnose_response(
        report_id=report.id,
        system_id=system.id,
        answer=answer_text,
        template_name=template["name"] if template else "",
        template_description=template["description"] if template else "自由诊断",
        model=run.model,
        duration_ms=run.duration_ms,
        total_tokens=run.total_tokens,
        evidence_sources=evidence_sources,
        tool_calls=run.tool_calls,
        evidence=evidence_items,
        evidence_steps=evidence_steps,
        knowledge_refs=knowledge_refs,
    )

"""诊断工具的 provider 构造：归档读取、代码沙箱、业务数据查询、远程命令。

这些工厂原本内联在 `services/diagnostics/service.py`（1300+ 行）里，搬出来是因为
它们只依赖"系统 + 会话 + org"，与诊断编排（runbook/拓扑/报告终态）无关。
每个工厂返回可注入 agent 的闭包；权限、审计与外部数据包装仍由 harness 统一处理。
"""
import json
import logging
from sqlmodel import Session

from app.core import metrics
from app.services.audit import record_audit_event
from app.services.collectors.exec import select_online_collector
from app.services.data_analysis import (
    analyze_system_data,
    describe_readonly_datasets,
    get_dataset_source_config,
    get_readonly_database_config,
    list_readonly_datasets,
    query_readonly_dataset,
)
from app.services.data_analysis import _remote_query_executor
from app.services.realtime.websocket import manager

log = logging.getLogger(__name__)


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
        token = save_artifact(name, raw, org_id=system.org_id, system_id=system.id)
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
            token = save_artifact(name, data, org_id=system.org_id, system_id=system.id)
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
        except Exception:  # noqa: BLE001 - 查询已执行，但审计失败必须留痕
            log.exception("[audit] 数据集查询审计写入失败 system=%s dataset=%s", system.id, meta.get("code"))
            metrics.inc("aiops_audit_write_failures_total", stage="dataset_query")
        return f"{header} 共 {len(rows)} 行（最多展示 40 行）：\n{payload}{total_line}"

    return query, catalog


def _user_id_from_actor(actor_id: str) -> int | None:
    if not actor_id:
        return None
    try:
        return int(actor_id)
    except ValueError:
        return None

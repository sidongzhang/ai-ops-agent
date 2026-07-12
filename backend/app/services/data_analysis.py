"""Read-only business data analysis helpers."""
from __future__ import annotations

import re
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import create_engine, text
from sqlmodel import Session

from app.core.security import decrypt_sensitive_fields, mask_sensitive_fields
from app.schemas.diagnostics import DataAnalysisResponse, ReadonlyDatabaseConfig
from app.services.audit import record_audit_event
from app.repositories.systems import list_enabled_services_for_system
from app.services.descriptors.builder import system_to_descriptor
from app.services.descriptors.health import collect_health, read_service_logs
from app.services.systems.service import require_system
from app.services.collectors.exec import select_online_collector
from app.services.realtime.websocket import manager

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DANGEROUS_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|replace|grant|revoke|vacuum|attach|detach|copy)\b",
    re.IGNORECASE,
)
_DATA_KEYWORDS = ("到达", "新增", "数量", "统计", "最近", "业务", "记录", "订单", "任务")
_RECENT_KEYWORDS = ("最近", "最新", "样例", "明细", "记录")
_TODAY_KEYWORDS = ("今天", "今日", "当天", "today", "新增", "到达")
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100
_STUCK_TASK_KEYWORDS = ("一直处理中", "卡住", "卡死", "超时任务", "任务积压", "处理不完", "长期处理中")


def is_data_analysis_question(question: str) -> bool:
    normalized = (question or "").lower()
    return any(keyword in normalized for keyword in (*_DATA_KEYWORDS, *_STUCK_TASK_KEYWORDS))


def is_stuck_task_question(question: str) -> bool:
    normalized = (question or "").lower()
    return any(keyword in normalized for keyword in _STUCK_TASK_KEYWORDS)


def get_readonly_database_config(session: Session, system_id: int, org_id: int) -> ReadonlyDatabaseConfig:
    system = require_system(session, system_id, org_id)
    config = _data_source_config(system.infra)
    return ReadonlyDatabaseConfig(**mask_sensitive_fields(config)) if config else ReadonlyDatabaseConfig()


def analyze_system_data(
    session: Session,
    system_id: int,
    org_id: int,
    question: str,
    *,
    actor_type: str = "user",
    actor_id: str = "",
) -> DataAnalysisResponse:
    system = require_system(session, system_id, org_id)
    config = _data_source_config(system.infra)
    if not config or config.get("enabled") is False:
        raise ValueError("当前系统未配置只读数据分析数据源")
    remote_executor = _remote_query_executor(session, system)
    if is_stuck_task_question(question):
        return analyze_stuck_tasks(
            session,
            system,
            config,
            question,
            actor_type=actor_type,
            actor_id=actor_id,
            remote_executor=remote_executor,
        )

    table = _safe_identifier(config.get("table"), "table")
    timestamp_column = config.get("timestamp_column")
    if timestamp_column:
        timestamp_column = _safe_identifier(timestamp_column, "timestamp_column")

    max_rows = _limit(config.get("max_rows", _DEFAULT_LIMIT))
    today_only = _wants_today(question) and bool(timestamp_column)
    where_sql = f" WHERE {timestamp_column} >= :start_time" if today_only else ""
    params: dict[str, Any] = {}
    if today_only:
        params["start_time"] = datetime.now().date().isoformat()

    count_sql = f"SELECT COUNT(*) AS total FROM {table}{where_sql}"
    count_rows = run_readonly_query(config, count_sql, params, max_rows=1, executor=remote_executor)
    total = int(count_rows[0]["total"]) if count_rows else 0

    rows: list[dict[str, Any]] = []
    if _wants_recent(question):
        order_sql = f" ORDER BY {timestamp_column} DESC" if timestamp_column else ""
        sample_sql = f"SELECT * FROM {table}{where_sql}{order_sql} LIMIT :limit"
        rows = run_readonly_query(config, sample_sql, {**params, "limit": max_rows}, max_rows=max_rows, executor=remote_executor)
        rows = _mask_rows(rows, config.get("sensitive_fields", []))
        rows = _serialize_rows(rows)

    scope = "今天" if today_only else "当前条件"
    answer_lines = [
        f"已查询只读数据源 `{config.get('name', table)}` 的 `{table}` 表。",
        f"{scope}匹配记录数：{total} 条。",
    ]
    if rows:
        answer_lines.append(f"已返回最近 {len(rows)} 条样例，敏感字段已脱敏。")
    answer_lines.append("本次仅执行 SELECT 查询，没有修改任何业务数据。")

    evidence = {
        "data_source": config.get("name", table),
        "table": table,
        "timestamp_column": timestamp_column or "",
        "today_only": today_only,
        "count_sql": count_sql,
        "params": params,
        "total": total,
        "sample_rows": rows,
    }
    record_audit_event(
        session,
        org_id=org_id,
        system_id=system.id,
        actor_type=actor_type,
        actor_id=actor_id,
        event_type="data_analysis.queried",
        target_type="table",
        target_id=table,
        status="success",
        input={"question": question},
        output={"total": total, "sample_rows": len(rows), "today_only": today_only},
        commit=True,
    )
    return DataAnalysisResponse(system_id=system.id, answer="\n".join(answer_lines), evidence=evidence)


def analyze_stuck_tasks(
    session: Session,
    system,
    config: dict,
    question: str,
    *,
    actor_type: str = "user",
    actor_id: str = "",
    remote_executor: Callable | None = None,
) -> DataAnalysisResponse:
    if not config.get("task_analysis_enabled"):
        raise ValueError("当前只读数据源尚未启用任务卡住专项分析")

    table = _safe_identifier(config.get("table"), "table")
    status_column = _safe_identifier(config.get("status_column"), "status_column")
    updated_column = _safe_identifier(config.get("updated_at_column"), "updated_at_column")
    task_id_column = (
        _safe_identifier(config.get("task_id_column"), "task_id_column")
        if config.get("task_id_column")
        else ""
    )
    worker_column = (
        _safe_identifier(config.get("worker_column"), "worker_column")
        if config.get("worker_column")
        else ""
    )
    processing_values = [
        str(value).strip() for value in config.get("processing_values", []) if str(value).strip()
    ]
    if not processing_values:
        raise ValueError("任务卡住分析未配置处理中状态值")

    threshold_minutes = max(1, min(int(config.get("stuck_threshold_minutes") or 30), 10080))
    stuck_before = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)
    status_params = {f"processing_{index}": value for index, value in enumerate(processing_values)}
    placeholders = ", ".join(f":{key}" for key in status_params)
    selected_columns = [column for column in (task_id_column, status_column, updated_column, worker_column) if column]
    select_sql = ", ".join(dict.fromkeys(selected_columns))
    max_rows = _limit(config.get("max_rows", _DEFAULT_LIMIT))
    stuck_sql = (
        f"SELECT {select_sql} FROM {table} "
        f"WHERE {status_column} IN ({placeholders}) AND {updated_column} < :stuck_before "
        f"ORDER BY {updated_column} ASC LIMIT :limit"
    )
    stuck_rows = run_readonly_query(
        config,
        stuck_sql,
        {**status_params, "stuck_before": stuck_before.isoformat(), "limit": max_rows},
        max_rows=max_rows,
        executor=remote_executor,
    )
    stuck_rows = _mask_rows(stuck_rows, config.get("sensitive_fields", []))
    stuck_rows = _serialize_rows(stuck_rows)

    count_sql = (
        f"SELECT COUNT(*) AS total FROM {table} "
        f"WHERE {status_column} IN ({placeholders}) AND {updated_column} < :stuck_before"
    )
    count_rows = run_readonly_query(
        config,
        count_sql,
        {**status_params, "stuck_before": stuck_before.isoformat()},
        max_rows=1,
        executor=remote_executor,
    )
    stuck_count = int(count_rows[0]["total"]) if count_rows else 0
    status_sql = (
        f"SELECT {status_column} AS task_status, COUNT(*) AS total "
        f"FROM {table} GROUP BY {status_column} ORDER BY total DESC"
    )
    status_summary = run_readonly_query(config, status_sql, max_rows=_MAX_LIMIT, executor=remote_executor)

    descriptor = system_to_descriptor(
        system,
        list_enabled_services_for_system(session, system.id),
    )
    configured_workers = {str(name).strip() for name in config.get("worker_service_names", []) if str(name).strip()}
    if configured_workers:
        worker_services = [
            service for service in descriptor.get("services", []) if service.get("name") in configured_workers
        ]
    else:
        worker_markers = ("worker", "consumer", "executor", "processor", "任务", "消费")
        worker_services = [
            service
            for service in descriptor.get("services", [])
            if any(marker in service.get("name", "").lower() for marker in worker_markers)
        ]
    worker_descriptor = {**descriptor, "services": worker_services}
    worker_health = collect_health(worker_descriptor) if worker_services else []
    missing_workers = sorted(configured_workers - {service.get("name") for service in worker_services})
    worker_health.extend(
        {"name": name, "ok": False, "detail": "配置的 Worker 服务未注册", "connector": ""}
        for name in missing_workers
    )
    worker_logs = []
    for item in worker_health:
        if item.get("ok") or item.get("name") in missing_workers:
            continue
        try:
            log_text = read_service_logs(descriptor, item["name"], 30)
            worker_logs.append({"service": item["name"], "excerpt": log_text[:1000]})
        except Exception as exc:  # noqa: BLE001
            worker_logs.append({"service": item["name"], "excerpt": f"日志读取失败：{exc}"})

    unhealthy_workers = [item for item in worker_health if not item.get("ok")]
    answer_lines = [
        f"已检查 `{table}` 表中状态为 {'、'.join(processing_values)} 的任务。",
        f"以最后更新时间超过 {threshold_minutes} 分钟未变化为卡住标准，共发现 {stuck_count} 条卡住任务。",
    ]
    if status_summary:
        distribution = "、".join(f"{item['task_status']}={item['total']}" for item in status_summary[:8])
        answer_lines.append(f"当前任务状态分布：{distribution}。")
    if worker_health:
        health_text = "、".join(
            f"{item['name']}={'正常' if item['ok'] else '异常'}（{item['detail']}）"
            for item in worker_health
        )
        answer_lines.append(f"Worker 检查：{health_text}。")
    else:
        answer_lines.append("当前没有匹配到已注册的 Worker 服务，无法自动确认消费进程状态。")

    if stuck_count == 0:
        answer_lines.append("结论：当前未发现超过阈值仍处于处理中的任务。")
    elif unhealthy_workers:
        answer_lines.append("建议先恢复异常 Worker，再观察卡住任务的最后更新时间是否继续推进；恢复操作需人工确认。")
    else:
        answer_lines.append("Worker 当前可达，建议继续检查任务锁、下游依赖、重试队列和错误日志，避免直接批量重跑。")
    if stuck_rows:
        answer_lines.append(f"已返回最早卡住的 {len(stuck_rows)} 条任务样例，敏感字段已脱敏。")
    answer_lines.append("本次仅执行 SELECT 查询和只读健康检查，没有修改任务或重启服务。")

    evidence = {
        "analysis_type": "stuck_tasks",
        "data_source": config.get("name", table),
        "table": table,
        "status_column": status_column,
        "updated_at_column": updated_column,
        "processing_values": processing_values,
        "stuck_threshold_minutes": threshold_minutes,
        "stuck_before": stuck_before.isoformat(),
        "stuck_count": stuck_count,
        "status_summary": status_summary,
        "sample_rows": stuck_rows,
        "worker_health": worker_health,
        "worker_logs": worker_logs,
        "count_sql": count_sql,
    }
    record_audit_event(
        session,
        org_id=system.org_id,
        system_id=system.id,
        actor_type=actor_type,
        actor_id=actor_id,
        event_type="task_stuck.analyzed",
        target_type="table",
        target_id=table,
        status="success",
        input={"question": question, "threshold_minutes": threshold_minutes},
        output={
            "stuck_count": stuck_count,
            "sample_rows": len(stuck_rows),
            "worker_count": len(worker_health),
            "unhealthy_workers": [item["name"] for item in unhealthy_workers],
        },
        commit=True,
    )
    return DataAnalysisResponse(system_id=system.id, answer="\n".join(answer_lines), evidence=evidence)


def run_readonly_query(
    config: dict,
    sql: str,
    params: dict[str, Any] | None = None,
    *,
    max_rows: int = _DEFAULT_LIMIT,
    executor: Callable | None = None,
) -> list[dict[str, Any]]:
    normalized = _normalize_sql(sql)
    if not normalized.lower().startswith("select"):
        raise ValueError("只读数据分析只允许 SELECT 查询")
    if ";" in normalized.rstrip(";"):
        raise ValueError("只读数据分析不允许一次执行多条 SQL")
    if _DANGEROUS_SQL.search(normalized):
        raise ValueError("只读数据分析拒绝执行包含写入或结构变更的 SQL")

    if executor:
        rows = executor(normalized, params or {}, _limit(max_rows))
        return rows[:_limit(max_rows)]

    url = config.get("database_url") or config.get("url")
    if not url:
        raise ValueError("只读数据源缺少 url")

    engine = create_engine(url, connect_args=_connect_args(url, config))
    with engine.connect() as conn:
        result = conn.execute(text(normalized), params or {})
        rows = [dict(row) for row in result.mappings().fetchmany(_limit(max_rows))]
        conn.rollback()
    engine.dispose()
    return rows


def _remote_query_executor(session: Session, system) -> Callable | None:
    """返回一个通过在线采集器执行只读 SQL 的闭包；不可用时保留直连回退。"""
    if system.local:
        return None
    collector = select_online_collector(session, system.id)
    if not collector or not manager.is_connected(collector.id):
        return None

    def execute(sql: str, params: dict, max_rows: int) -> list[dict[str, Any]]:
        try:
            result = asyncio.run(manager.send_command(
                collector.id,
                "run_readonly_query",
                {"sql": sql, "params": params, "max_rows": max_rows},
            ))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"远程只读数据库查询失败：{exc}") from exc
        if not result.get("ok"):
            raise RuntimeError(f"远程只读数据库查询失败：{result.get('result', '未知错误')}")
        rows = result.get("result", [])
        return rows if isinstance(rows, list) else []

    return execute


def _data_source_config(infra: dict | None) -> dict:
    infra = decrypt_sensitive_fields(infra or {})
    config = infra.get("readonly_database") or infra.get("data_analysis") or {}
    return dict(config) if isinstance(config, dict) else {}


def _normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", (sql or "").strip())


def _safe_identifier(value: Any, field: str) -> str:
    text_value = str(value or "").strip()
    if not _IDENTIFIER.match(text_value):
        raise ValueError(f"只读数据源字段 {field} 只能使用安全的表名或列名")
    return text_value


def _limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT
    return max(1, min(limit, _MAX_LIMIT))


def _serialize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for row in rows:
        serialized.append({key: _serialize_value(value) for key, value in row.items()})
    return serialized


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize_value(item) for key, item in value.items()}
    return value


def _connect_args(url: str, config: dict) -> dict:
    timeout = float(config.get("timeout_seconds", 5))
    if url.startswith("sqlite"):
        return {"timeout": timeout}
    return {}


def _wants_today(question: str) -> bool:
    lowered = (question or "").lower()
    return any(keyword in lowered for keyword in _TODAY_KEYWORDS)


def _wants_recent(question: str) -> bool:
    lowered = (question or "").lower()
    return any(keyword in lowered for keyword in _RECENT_KEYWORDS)


def _mask_rows(rows: list[dict[str, Any]], sensitive_fields: list[str]) -> list[dict[str, Any]]:
    sensitive = {field.lower() for field in sensitive_fields}
    masked = []
    for row in rows:
        masked.append({
            key: ("***" if key.lower() in sensitive and value not in (None, "") else value)
            for key, value in row.items()
        })
    return masked

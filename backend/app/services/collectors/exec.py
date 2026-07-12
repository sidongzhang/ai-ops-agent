"""Application service for collector command execution."""
import logging

from sqlmodel import Session

from app.models.collectors import Collector
from app.repositories.collectors import get_first_collector_for_system, list_collectors_for_system
from app.schemas import CollectorExecRequest, CollectorExecResponse
from app.services.realtime.websocket import manager
from app.services.systems.service import require_system

log = logging.getLogger(__name__)

ALLOWED_COLLECTOR_COMMANDS = {
    "fetch_logs", "search_logs", "health_check", "query_prometheus",
    "run_readonly_query", "run_redis_command", "run_kafka_command", "restart_container", "restart_systemd",
}


def select_online_collector(session: Session, system_id: int) -> Collector | None:
    """Choose a connected collector, preferring the most recently reported one."""
    collectors = list_collectors_for_system(session, system_id)
    connected = [collector for collector in collectors if manager.is_connected(collector.id)]
    if connected:
        return max(
            connected,
            key=lambda item: (item.last_seen is not None, item.last_seen, item.id or 0),
        )
    return get_first_collector_for_system(session, system_id)


async def execute_collector_command(
    session: Session,
    system_id: int,
    org_id: int,
    body: CollectorExecRequest,
) -> CollectorExecResponse:
    if body.cmd not in ALLOWED_COLLECTOR_COMMANDS:
        allowed = ", ".join(sorted(ALLOWED_COLLECTOR_COMMANDS))
        raise ValueError(f"不支持的命令「{body.cmd}」，可用: {allowed}")

    require_system(session, system_id, org_id)
    if body.collector_id is not None:
        collector = session.get(Collector, body.collector_id)
        if not collector or collector.system_id != system_id or collector.org_id != org_id:
            raise LookupError("采集器不存在或不属于当前系统")
    else:
        collector = select_online_collector(session, system_id)
    if not collector:
        raise LookupError("该系统还没有采集器，请先在控制台创建")
    _require_connected(collector)

    result = await manager.send_command(collector.id, body.cmd, body.args)
    log.info(f"[exec] system={system_id} cmd={body.cmd} ok={result.get('ok')}")
    return CollectorExecResponse(
        ok=result.get("ok", False),
        result=result.get("result", ""),
        collector_id=collector.id,
    )


def _require_connected(collector: Collector) -> None:
    if not manager.is_connected(collector.id):
        raise RuntimeError("采集器当前离线（WebSocket 未连接），请确认采集器进程正在运行")

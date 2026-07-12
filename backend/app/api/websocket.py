"""
采集器 WebSocket 下行通道。

采集器连接此端点后，平台可主动下发命令（fetch_logs / search_logs / health_check /
query_prometheus / run_readonly_query / run_redis_command / run_kafka_command /
restart_container / restart_systemd）。
Auth：采集器将 X-Collector-Key 作为 query 参数 key 传入（WebSocket 握手阶段无法传 Header）。
"""
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlmodel import Session, select

from ..core.database import engine
from ..models.collectors import Collector
from ..core.security import hash_collector_key
from ..services.realtime.websocket import manager

router = APIRouter(tags=["ws"])
log = logging.getLogger(__name__)


@router.websocket("/ws/collector")
async def collector_ws(
    websocket: WebSocket,
    key: str = Query(..., description="X-Collector-Key（采集器密钥明文）"),
):
    # 鉴权：按 key hash 查询采集器
    with Session(engine) as session:
        collector = session.exec(
            select(Collector).where(Collector.token_hash == hash_collector_key(key))
        ).first()

    if not collector:
        await websocket.close(code=4001, reason="无效的采集器密钥")
        return

    collector_id = collector.id
    await manager.connect(collector_id, websocket)
    log.info(f"[ws] 采集器 {collector_id} 已连接（system_id={collector.system_id}）")

    try:
        while True:
            data = await websocket.receive_json()
            manager.resolve(data)
    except WebSocketDisconnect:
        log.info(f"[ws] 采集器 {collector_id} 断开连接")
    except Exception as e:
        log.warning(f"[ws] 采集器 {collector_id} 异常: {e}")
    finally:
        manager.disconnect(collector_id)

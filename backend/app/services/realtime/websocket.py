"""
WebSocket 连接管理器（单例）。

维护「collector_id → WebSocket」的活跃连接表。
平台通过 send_command() 向指定采集器下发命令，等待结果（asyncio.Future + 超时）。
采集器返回结果时调用 resolve()，唤醒等待的协程。
"""
import asyncio
import uuid
from typing import Optional

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._conns: dict[int, WebSocket] = {}           # collector_id → ws
        self._pending: dict[str, asyncio.Future] = {}    # request_id → future

    async def connect(self, collector_id: int, ws: WebSocket) -> None:
        await ws.accept()
        self._conns[collector_id] = ws

    def disconnect(self, collector_id: int) -> None:
        self._conns.pop(collector_id, None)

    def is_connected(self, collector_id: int) -> bool:
        return collector_id in self._conns

    async def send_command(
        self,
        collector_id: int,
        cmd: str,
        args: Optional[dict] = None,
        timeout: float = 30.0,
    ) -> dict:
        ws = self._conns.get(collector_id)
        if not ws:
            raise RuntimeError("采集器未连接（WebSocket 未建立，采集器可能离线）")
        request_id = str(uuid.uuid4())
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[request_id] = future
        try:
            await ws.send_json({"cmd": cmd, "args": args or {}, "request_id": request_id})
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except asyncio.TimeoutError:
            raise RuntimeError(f"采集器 {collector_id} 命令超时（{timeout}s）")
        finally:
            self._pending.pop(request_id, None)

    def resolve(self, data: dict) -> None:
        """由 WS 接收循环调用，解析采集器返回的结果并唤醒等待的协程。"""
        rid = data.get("request_id")
        if rid and rid in self._pending:
            future = self._pending[rid]
            if not future.done():
                future.set_result(data)


# 全局单例
manager = ConnectionManager()

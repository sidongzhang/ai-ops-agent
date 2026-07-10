"""
采集器 WebSocket 下行通道客户端。

在独立线程+asyncio事件循环中持续连接平台的 /ws/collector 端点。
平台可通过此通道主动下发命令（fetch_logs / search_logs / health_check）。
断线后使用指数退避自动重连（最长 60s）。

命令格式（平台发送）：
  {"cmd": "fetch_logs", "args": {"service": "web", "lines": 50}, "request_id": "uuid"}

结果格式（采集器回传）：
  {"ok": true, "result": "日志内容...", "request_id": "uuid"}
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SHARED = os.path.join(_ROOT, 'shared')
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)
from connectors import get_connector  # noqa: E402


def _handle_command(cmd: str, args: dict, descriptor: dict) -> dict:
    """在本地执行平台下发的命令，返回结果 dict。"""
    try:
        if cmd == "fetch_logs":
            svc_name = args.get("service", "")
            lines = int(args.get("lines", 50))
            svc = _find_service(descriptor, svc_name)
            if not svc:
                return {"ok": False, "result": f"服务「{svc_name}」未注册"}
            text = get_connector(svc, descriptor).read_logs(lines)
            return {"ok": True, "result": text}

        elif cmd == "search_logs":
            svc_name = args.get("service", "")
            keyword = args.get("keyword", "ERROR")
            lines = int(args.get("lines", 200))
            svc = _find_service(descriptor, svc_name)
            if not svc:
                return {"ok": False, "result": f"服务「{svc_name}」未注册"}
            text = get_connector(svc, descriptor).search_logs(keyword, lines)
            return {"ok": True, "result": text}

        elif cmd == "health_check":
            svc_name = args.get("service", "")
            results = []
            for svc in descriptor.get("services", []):
                if svc_name and svc.get("name") != svc_name:
                    continue
                try:
                    ok, detail = get_connector(svc, descriptor).health()
                except Exception as e:
                    ok, detail = False, str(e)
                results.append({"name": svc.get("name", ""), "ok": ok, "detail": detail})
            return {"ok": True, "result": results}

        elif cmd == "restart_container":
            svc_name = args.get("service", "")
            container = str(args.get("container", "") or "").strip()
            svc = _find_service(descriptor, svc_name)
            if not svc:
                return {"ok": False, "result": {"error": f"服务「{svc_name}」未注册"}}
            expected = str(
                svc.get("runtime", {}).get("container")
                or svc.get("container")
                or svc.get("config", {}).get("container", "")
            ).strip()
            if not expected:
                return {"ok": False, "result": {"error": f"服务「{svc_name}」未配置可重启容器"}}
            if container != expected:
                return {"ok": False, "result": {"error": f"容器「{container}」与系统注册配置不一致"}}
            started_at = time.monotonic()
            result = subprocess.run(
                ["docker", "restart", expected],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                error = result.stderr.strip() or f"docker restart 失败，退出码 {result.returncode}"
                return {"ok": False, "result": {"error": error, "target": expected}}
            return {
                "ok": True,
                "result": {
                    "target": expected,
                    "duration_ms": int((time.monotonic() - started_at) * 1000),
                },
            }

        else:
            return {"ok": False, "result": f"未知命令: {cmd}"}

    except Exception as e:
        return {"ok": False, "result": f"执行出错: {e}"}


def _find_service(descriptor: dict, name: str):
    return next(
        (s for s in descriptor.get("services", []) if s.get("name") == name),
        None,
    )


async def _ws_loop(ws_url: str, collector_key: str, get_descriptor: Callable[[], dict]) -> None:
    """持续 WebSocket 连接，接收命令并回传结果。"""
    import websockets  # 运行时导入，采集器可选安装

    retry_delay = 5
    url = f"{ws_url}?key={collector_key}"

    while True:
        try:
            async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
                log.info(f"[ws] 已连接平台 {ws_url}")
                retry_delay = 5  # 连接成功后重置退避
                async for raw in ws:
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    cmd = data.get("cmd", "")
                    args = data.get("args", {})
                    request_id = data.get("request_id", "")
                    log.debug(f"[ws] 收到命令 cmd={cmd} args={args}")

                    result = _handle_command(cmd, args, get_descriptor())
                    await ws.send(json.dumps({**result, "request_id": request_id}))

        except Exception as e:
            log.warning(f"[ws] 连接断开，{retry_delay}s 后重连: {e}")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)


def start(platform_url: str, collector_key: str, get_descriptor: Callable[[], dict]) -> threading.Thread:
    """
    在后台 daemon 线程中启动 WebSocket 客户端。

    platform_url   : 平台 HTTP 地址（http:// 或 https://），内部自动转为 ws:// 或 wss://
    collector_key  : COLLECTOR_KEY 明文
    get_descriptor : 无参可调用，返回当前系统描述符（每次调用前刷新）
    """
    ws_url = (
        platform_url
        .replace("https://", "wss://")
        .replace("http://", "ws://")
        .rstrip("/") + "/ws/collector"
    )

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_ws_loop(ws_url, collector_key, get_descriptor))
        finally:
            loop.close()

    t = threading.Thread(target=_run, daemon=True, name="collector-ws")
    t.start()
    log.info(f"[ws] 客户端线程已启动 → {ws_url}")
    return t

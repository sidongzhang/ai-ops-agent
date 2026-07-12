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
import re
import shlex
import subprocess
import sys
import socket
import threading
import time
from typing import Callable

import requests

log = logging.getLogger(__name__)

_DANGEROUS_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|replace|grant|revoke|vacuum|attach|detach|copy)\b",
    re.IGNORECASE,
)

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

        elif cmd == "query_prometheus":
            svc_name = args.get("service", "")
            promql = str(args.get("query", "up") or "up")[:500]
            svc = _find_service(descriptor, svc_name)
            if not svc or svc.get("connector") != "prometheus":
                return {"ok": False, "result": f"Prometheus 服务「{svc_name}」未注册"}
            url = str(svc.get("url") or svc.get("config", {}).get("url", "")).rstrip("/")
            if not url:
                return {"ok": False, "result": "Prometheus 未配置地址"}
            response = requests.get(f"{url}/api/v1/query", params={"query": promql}, timeout=10)
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") != "success":
                return {"ok": False, "result": payload.get("error", "Prometheus 查询失败")}
            return {"ok": True, "result": payload.get("data", {}).get("result", [])[:50]}

        elif cmd == "query_prometheus_range":
            svc_name = args.get("service", "")
            promql = str(args.get("query", "up") or "up")[:500]
            start = args.get("start")
            end = args.get("end")
            step = str(args.get("step", "5m") or "5m")[:20]
            svc = _find_service(descriptor, svc_name)
            if not svc or svc.get("connector") != "prometheus":
                return {"ok": False, "result": f"Prometheus 服务「{svc_name}」未注册"}
            url = str(svc.get("url") or svc.get("config", {}).get("url", "")).rstrip("/")
            if not url:
                return {"ok": False, "result": "Prometheus 未配置地址"}
            try:
                start_val = float(start)
                end_val = float(end)
            except (TypeError, ValueError):
                return {"ok": False, "result": "start/end 参数无效"}
            response = requests.get(
                f"{url}/api/v1/query_range",
                params={"query": promql, "start": start_val, "end": end_val, "step": step},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") != "success":
                return {"ok": False, "result": payload.get("error", "Prometheus 范围查询失败")}
            return {"ok": True, "result": payload.get("data", {}).get("result", [])[:20]}

        elif cmd == "run_readonly_query":
            sql = " ".join(str(args.get("sql", "")).strip().split())
            if not sql.lower().startswith("select") or ";" in sql.rstrip(";") or _DANGEROUS_SQL.search(sql):
                return {"ok": False, "result": "安全限制：只允许单条 SELECT 查询"}
            database = ((descriptor.get("infra") or {}).get("readonly_database") or {})
            url = database.get("database_url") or database.get("url")
            if not url:
                return {"ok": False, "result": "未配置远程只读数据库连接串"}
            try:
                from sqlalchemy import create_engine, text

                max_rows = max(1, min(int(args.get("max_rows", 20) or 20), 100))
                engine = create_engine(url, connect_args=_database_connect_args(url, database))
                with engine.connect() as connection:
                    result = connection.execute(text(sql), args.get("params") or {})
                    rows = []
                    for row in result.mappings().fetchmany(max_rows):
                        rows.append({key: _json_value(value) for key, value in row.items()})
                    connection.rollback()
                engine.dispose()
                return {"ok": True, "result": rows}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "result": f"数据库查询失败: {exc}"}

        elif cmd == "run_redis_command":
            command = str(args.get("command", "")).strip()
            parts = command.split()
            first = parts[0].upper() if parts else ""
            allowed = {
                "INFO", "DBSIZE", "CLIENT", "CONFIG", "SLOWLOG", "KEYS", "TTL",
                "TYPE", "LLEN", "SCARD", "ZCARD", "HLEN", "STRLEN", "OBJECT",
            }
            if first not in allowed:
                return {"ok": False, "result": f"安全限制：拒绝执行 Redis 命令「{first}」"}
            second = parts[1].upper() if len(parts) > 1 else ""
            readonly_subcommands = {
                "CLIENT": {"LIST", "INFO"},
                "CONFIG": {"GET"},
                "SLOWLOG": {"GET", "LEN"},
            }
            if first in readonly_subcommands and second not in readonly_subcommands[first]:
                return {"ok": False, "result": f"安全限制：Redis {first} 仅允许只读子命令"}
            svc = _find_service(descriptor, str(args.get("service", "")))
            if not svc:
                svc = next(
                    (item for item in descriptor.get("services", [])
                     if item.get("connector") == "tcp"
                     and int(item.get("config", {}).get("port", item.get("port", 0)) or 0) == 6379),
                    None,
                )
            if not svc:
                return {"ok": False, "result": "未找到已注册 Redis 服务"}
            config = svc.get("config", {})
            host = svc.get("host") or config.get("host", "127.0.0.1")
            port = int(svc.get("port") or config.get("port", 6379))
            payload = f"*{len(parts)}\r\n" + "".join(
                f"${len(part)}\r\n{part}\r\n" for part in parts
            )
            with socket.create_connection((host, port), timeout=5) as conn:
                conn.sendall(payload.encode())
                raw = conn.recv(65536).decode("utf-8", errors="replace")
            return {"ok": True, "result": raw[:5000] or "(无响应)"}

        elif cmd == "run_kafka_command":
            parts = shlex.split(str(args.get("command", "")))
            if not parts or parts[0] not in ("topics", "consumer-groups"):
                return {"ok": False, "result": "安全限制：只允许 topics 或 consumer-groups 子命令"}
            dangerous = {"--create", "--delete", "--alter", "--reset-offsets", "--execute"}
            if any(part in dangerous for part in parts):
                return {"ok": False, "result": "安全限制：拒绝 Kafka 写操作"}
            svc = _find_service(descriptor, str(args.get("service", "")))
            if not svc:
                svc = next((item for item in descriptor.get("services", []) if "kafka" in item.get("name", "").lower()), None)
            container = str((svc or {}).get("runtime", {}).get("container") or (svc or {}).get("config", {}).get("container", "")).strip()
            if not container:
                return {"ok": False, "result": "Kafka 服务未配置可执行的 Docker 容器"}
            script = f"kafka-{parts[0]}.sh"
            command = ["docker", "exec", container, f"/opt/kafka/bin/{script}", "--bootstrap-server", "localhost:9092"] + parts[1:]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            output = (result.stdout + result.stderr).strip()
            return {"ok": result.returncode == 0, "result": output[:5000] or "(无输出)"}

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

        elif cmd == "restart_systemd":
            svc_name = str(args.get("service", "") or "").strip()
            unit = str(args.get("unit", "") or "").strip()
            svc = _find_service(descriptor, svc_name)
            if not svc:
                return {"ok": False, "result": {"error": f"服务「{svc_name}」未注册"}}
            expected = str(
                svc.get("runtime", {}).get("systemd_unit")
                or svc.get("systemd_unit")
                or svc.get("config", {}).get("systemd_unit", "")
            ).strip()
            if not expected:
                return {"ok": False, "result": {"error": f"服务「{svc_name}」未配置 systemd_unit"}}
            if unit != expected:
                return {"ok": False, "result": {"error": "systemd 服务名与系统注册配置不一致"}}
            started_at = time.monotonic()
            if svc.get("connector") == "ssh":
                ok, detail = get_connector(svc, descriptor).restart_systemd()
                return {"ok": ok, "result": {"target": expected, "detail": detail}}
            safe_unit = shlex.quote(expected)
            result = subprocess.run(
                ["sh", "-c", f"systemctl restart {safe_unit} && systemctl is-active {safe_unit}"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            detail = (result.stdout or result.stderr).strip()
            return {
                "ok": result.returncode == 0 and detail.splitlines()[-1:] == ["active"],
                "result": {
                    "target": expected,
                    "detail": detail or "重启失败",
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


def _database_connect_args(url: str, config: dict) -> dict:
    if str(url).startswith("sqlite"):
        return {"timeout": float(config.get("timeout_seconds", 5) or 5)}
    return {}


def _json_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


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

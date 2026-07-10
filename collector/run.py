#!/usr/bin/env python3
"""
可下载采集器（Collector）。

装在客户自己的网络里，**出站**连到平台（不需要客户开放任何入站端口、
也不用把内网地址/凭据交给平台保管）。双通道运行：

  上行（HTTP 轮询）：
    1. 拉取本采集器该探测哪些服务（GET /collector/config）
    2. 在本地用 connectors 探活（http/tcp/ssh/prometheus/k8s）
    3. 把结果出站推回平台（POST /collector/report）

  下行（WebSocket 持久连接）：
    平台可随时下发命令（fetch_logs / search_logs / health_check），
    采集器立即执行并实时回传结果，无需客户开放入站端口。

用法：
  PLATFORM_URL=https://platform.example.com \
  COLLECTOR_KEY=<创建采集器时拿到的密钥> \
  python collector/run.py            # 持续运行（含 WS 下行通道）
  python collector/run.py --once     # 跑一轮即退出（便于测试/cron，跳过 WS）
  COLLECTOR_WS=false python collector/run.py  # 禁用下行通道
"""
__version__ = "934d8bc"
import logging
import os
import sys
import time

import requests

# 复用 shared/connectors（共享连接器 SDK）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SHARED = os.path.join(_ROOT, 'shared')
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)
from connectors import get_connector  # noqa: E402

PLATFORM_URL = os.getenv("PLATFORM_URL", "http://localhost:8000").rstrip("/")
COLLECTOR_KEY = os.getenv("COLLECTOR_KEY", "")
INTERVAL = int(os.getenv("COLLECTOR_INTERVAL", "30"))
ENABLE_WS = os.getenv("COLLECTOR_WS", "true").lower() not in ("false", "0", "no")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [collector] %(message)s")
log = logging.getLogger(__name__)

# 缓存最近一次从平台拉到的描述符，供 WebSocket 下行命令使用
_last_descriptor: dict = {}


def _headers() -> dict:
    return {"X-Collector-Key": COLLECTOR_KEY}


def fetch_config() -> dict:
    r = requests.get(f"{PLATFORM_URL}/collector/config", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def probe(descriptor: dict) -> list:
    results = []
    for svc in descriptor.get("services", []):
        try:
            ok, detail = get_connector(svc, descriptor).health()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"检查失败: {e}"
        results.append({"name": svc.get("name", ""), "ok": ok,
                        "detail": detail, "connector": svc.get("connector", "")})
    return results


def report(results: list) -> None:
    r = requests.post(f"{PLATFORM_URL}/collector/report", headers=_headers(),
                      json={"services": results}, timeout=10)
    r.raise_for_status()


def run_once() -> None:
    global _last_descriptor
    cfg = fetch_config()
    _last_descriptor = cfg  # 更新缓存，供 WS 命令处理器使用
    results = probe(cfg)
    report(results)
    summary = ", ".join(f"{x['name']}={'OK' if x['ok'] else 'FAIL'}" for x in results)
    log.info(f"已上报系统「{cfg.get('name')}」{len(results)} 个服务: {summary}")


def _get_descriptor() -> dict:
    return _last_descriptor


def _start_ws() -> None:
    """尝试启动 WebSocket 下行通道，依赖 websockets 包（可选）。"""
    try:
        import ws_client  # 与 run.py 同目录
        ws_client.start(PLATFORM_URL, COLLECTOR_KEY, _get_descriptor)
    except ImportError:
        log.warning("[ws] 未找到 ws_client 模块，下行通道不可用")
    except Exception as e:
        log.warning(f"[ws] 启动失败（跳过）: {e}")


def main() -> None:
    if not COLLECTOR_KEY:
        print(f"采集器版本: {__version__}\n正确用法: PLATFORM_URL=... COLLECTOR_KEY=... /path/to/aiops-collector [--once]")
        sys.exit(1)
    if "--version" in sys.argv or "-v" in sys.argv:
        print(f"aiops-collector v{__version__}")
        sys.exit(0)
    once = "--once" in sys.argv
    log.info(f"采集器启动 → 平台 {PLATFORM_URL}，间隔 {INTERVAL}s，模式={'单次' if once else '持续'}")

    # 先跑一轮拉取描述符，再启动 WS（确保描述符有数据）
    try:
        run_once()
    except Exception as e:
        log.error(f"首轮采集出错: {e}")

    if not once and ENABLE_WS:
        _start_ws()
    elif once:
        return

    while True:
        try:
            run_once()
        except Exception as e:  # noqa: BLE001
            log.error(f"采集循环出错: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()

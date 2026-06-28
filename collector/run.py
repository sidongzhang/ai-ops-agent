#!/usr/bin/env python3
"""
可下载采集器（Collector）。

装在客户自己的网络里，**出站**连到平台（不需要客户开放任何入站端口、
也不用把内网地址/凭据交给平台保管）。循环执行：
  1. 拉取本采集器该探测哪些服务（GET /collector/config）
  2. 在本地用 connectors 探活（http/tcp/ssh/prometheus/k8s）
  3. 把结果出站推回平台（POST /collector/report）

用法：
  PLATFORM_URL=https://platform.example.com \
  COLLECTOR_KEY=<创建采集器时拿到的密钥> \
  python collector/run.py            # 持续运行
  python collector/run.py --once     # 跑一轮即退出（便于测试/cron）
"""
import logging
import os
import sys
import time

import requests

# 复用平台同一套 connectors（采集器=连接器跑在客户侧）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from connectors import get_connector  # noqa: E402

PLATFORM_URL = os.getenv("PLATFORM_URL", "http://localhost:8000").rstrip("/")
COLLECTOR_KEY = os.getenv("COLLECTOR_KEY", "")
INTERVAL = int(os.getenv("COLLECTOR_INTERVAL", "30"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [collector] %(message)s")
log = logging.getLogger(__name__)


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
    cfg = fetch_config()
    results = probe(cfg)
    report(results)
    summary = ", ".join(f"{x['name']}={'OK' if x['ok'] else 'FAIL'}" for x in results)
    log.info(f"已上报系统「{cfg.get('name')}」{len(results)} 个服务: {summary}")


def main() -> None:
    if not COLLECTOR_KEY:
        print("错误：请设置 COLLECTOR_KEY 环境变量（创建采集器时返回的密钥）")
        sys.exit(1)
    once = "--once" in sys.argv
    log.info(f"采集器启动 → 平台 {PLATFORM_URL}，间隔 {INTERVAL}s，模式={'单次' if once else '持续'}")
    while True:
        try:
            run_once()
        except Exception as e:  # noqa: BLE001
            log.error(f"采集循环出错: {e}")
        if once:
            break
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()

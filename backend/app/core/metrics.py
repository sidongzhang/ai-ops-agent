"""进程内指标（Prometheus 文本格式）。

只做计数器/仪表，不引入额外依赖。目的很具体：**把"静默失效"变成可采集的信号**。
平台的 AI 层坚持 fail-closed（审计写不进就不执行工具），但基础设施层此前多处
`except: pass`。这些失败如果没有计数，线上就永远发现不了。
"""
from __future__ import annotations

import threading
import time
from collections import Counter

_lock = threading.Lock()
_counters: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
_gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
_started_at = time.time()

_HELP = {
    "aiops_up": ("gauge", "控制面进程存活"),
    "aiops_db_up": ("gauge", "数据库连通性（1=正常）"),
    "aiops_process_start_time_seconds": ("gauge", "进程启动时间（Unix 秒）"),
    "aiops_audit_write_failures_total": ("counter", "审计写入失败次数（按 stage）"),
    "aiops_sandbox_blocked_total": ("counter", "沙箱拒绝执行次数（防止静默降级到进程内）"),
    "aiops_unknown_connector_total": ("counter", "未知连接器类型次数（按 kind）"),
}


def _key(name: str, labels: dict[str, str]) -> tuple[str, tuple[tuple[str, str], ...]]:
    return name, tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def inc(name: str, amount: int = 1, **labels: str) -> None:
    with _lock:
        _counters[_key(name, labels)] += amount


def set_gauge(name: str, value: float, **labels: str) -> None:
    with _lock:
        _gauges[_key(name, labels)] = float(value)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _fmt_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    body = ",".join(f'{k}="{_escape(v)}"' for k, v in labels)
    return f"{{{body}}}"


def render() -> str:
    """渲染成 Prometheus 文本格式（按指标名分组，稳定排序）。"""
    with _lock:
        counters = sorted(_counters.items())
        gauges = dict(_gauges)

    gauges[("aiops_process_start_time_seconds", ())] = _started_at

    grouped: dict[str, list[tuple[tuple[tuple[str, str], ...], float]]] = {}
    for (name, labels), value in counters:
        grouped.setdefault(name, []).append((labels, float(value)))
    for (name, labels), value in sorted(gauges.items()):
        grouped.setdefault(name, []).append((labels, value))

    lines: list[str] = []
    for name in sorted(grouped):
        kind, help_text = _HELP.get(name, ("gauge", name))
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {kind}")
        for labels, value in sorted(grouped[name], key=lambda item: item[0]):
            rendered = f"{value:g}" if isinstance(value, float) and value.is_integer() else str(value)
            lines.append(f"{name}{_fmt_labels(labels)} {rendered}")
    return "\n".join(lines) + "\n"


def reset() -> None:
    """测试用：清空所有指标。"""
    with _lock:
        _counters.clear()
        _gauges.clear()

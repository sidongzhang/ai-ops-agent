"""容器状态探测：`docker inspect` 的**唯一实现**。

被三处共用（此前各自拼同一段 format 串，改一处漏两处）：
  * 诊断 Agent 工具     backend/app/agent/diagnostics/tools.py  (check_container_state)
  * 容器巡检           backend/app/services/monitoring/oom_watch.py
  * MCP 运维工具服务    backend/app/agent/diagnostics/mcp_server.py

只做「取状态」这一件事，不做告警/格式化决策：
  * inspect_container()       → 结构化 dict（失败返回 None）
  * format_container_state()  → 人类/LLM 可读的多行文本
  * state_signature()         → 状态指纹（巡检去重：同一次故障不重复告警）
"""
import subprocess

# 单点维护的 docker inspect 输出格式（含 OOM 判定所需的全部字段）
INSPECT_FORMAT = (
    "Status={{.State.Status}}|OOMKilled={{.State.OOMKilled}}|ExitCode={{.State.ExitCode}}"
    "|Restarts={{.RestartCount}}|Memory={{.HostConfig.Memory}}"
    "|OOMScoreAdj={{.HostConfig.OomScoreAdj}}|FinishedAt={{.State.FinishedAt}}"
)

_FIELD_LABELS = {
    "Status": "状态",
    "OOMKilled": "OOM被杀(OOMKilled)",
    "ExitCode": "退出码(ExitCode)",
    "Restarts": "重启次数",
    "Memory": "内存上限(Memory limit)",
    "OOMScoreAdj": "OOM权重(OomScoreAdj)",
    "FinishedAt": "退出时间",
}


def inspect_container(container: str, *, timeout: int = 15) -> dict | None:
    """读取容器状态；docker 不可用或容器不存在时返回 None（调用方自行降级）。"""
    if not container:
        return None
    try:
        proc = subprocess.run(
            ["docker", "inspect", container, "--format", INSPECT_FORMAT],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception:  # noqa: BLE001 - docker 不可用/超时：静默降级
        return None
    if proc.returncode != 0:
        return None
    fields = dict(kv.split("=", 1) for kv in proc.stdout.strip().split("|") if "=" in kv)
    try:
        exit_code = int(fields.get("ExitCode", "0") or 0)
    except ValueError:
        exit_code = 0
    try:
        restarts = int(fields.get("Restarts", "0") or 0)
    except ValueError:
        restarts = 0
    return {
        "container": container,
        "status": fields.get("Status", "unknown"),
        "oom_killed": fields.get("OOMKilled") == "true",
        "exit_code": exit_code,
        "restarts": restarts,
        "memory_limit": fields.get("Memory", ""),
        "oom_score_adj": fields.get("OOMScoreAdj", ""),
        "finished_at": fields.get("FinishedAt", ""),
    }


def format_container_state(state: dict | None) -> str:
    """把状态 dict 渲染成多行文本（Agent / MCP 工具返回值）。"""
    if not state:
        return "容器状态不可用（docker inspect 失败或容器不存在）"
    container = state.get("container", "?")
    lines = [f"容器 {container} 状态:"]
    for key in ("Status", "OOMKilled", "ExitCode", "Restarts", "Memory", "OOMScoreAdj", "FinishedAt"):
        if key not in state:
            continue
        value = state[key]
        if isinstance(value, bool):
            value = "true" if value else "false"
        lines.append(f"  {_FIELD_LABELS[key]}: {value}")
    return "\n".join(lines)


def state_signature(state: dict | None) -> str:
    """状态指纹：变化才告警/恢复（同一次故障不重复提醒）。"""
    if not state:
        return "none"
    if state.get("oom_killed"):
        return "oom"
    if state.get("status") == "exited":
        return f"exited:{state.get('exit_code')}"
    if state.get("status") == "running":
        return "running"
    return f"{state.get('status')}:{state.get('exit_code')}"


__all__ = ["INSPECT_FORMAT", "inspect_container", "format_container_state", "state_signature"]

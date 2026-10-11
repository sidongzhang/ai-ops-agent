#!/usr/bin/env python3
"""演示用 MCP server（只读）：模拟一个外部 Kubernetes 集群的查询能力。

用途：演示「接一个 MCP server，agent 立刻能用它的工具」——不写一行 agent 代码。
配置（.env）：
  MCP_SERVERS=[{"name":"k8s","command":"{python}","args":["deploy/mcp/demo_k8s_server.py"],
                "description":"K8s 集群只读查询（Pod/事件/日志）"}]

用官方 mcp SDK 的 MCPServer（mcp 2.x，原名 FastMCP），stdio 传输；所有工具只读。
替换成真实 server 时，把 command/args 指向真实实现（如 npx @modelcontextprotocol/server-kubernetes）。
"""
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("demo-k8s")

_PODS = {
    "aiops-prod": [
        {"name": "algp-web-7d9f-2xk", "status": "Running", "restarts": 0, "node": "node-01"},
        {"name": "algp-datafinder-5c4d-9qz", "status": "Running", "restarts": 1, "node": "node-02"},
        {"name": "algp-worker-6f8b-lm4", "status": "CrashLoopBackOff", "restarts": 7, "node": "node-03"},
    ],
    "aiops-infra": [
        {"name": "kafka-0", "status": "Running", "restarts": 0, "node": "node-04"},
        {"name": "redis-0", "status": "Running", "restarts": 0, "node": "node-05"},
    ],
}

_EVENTS = [
    {
        "namespace": "aiops-prod",
        "involvedObject": "pod/algp-worker-6f8b-lm4",
        "reason": "BackOff",
        "message": "Back-off restarting failed container algp-worker",
        "count": 14,
    },
    {
        "namespace": "aiops-prod",
        "involvedObject": "pod/algp-datafinder-5c4d-9qz",
        "reason": "Unhealthy",
        "message": "Readiness probe failed: HTTP probe failed with statuscode: 503",
        "count": 3,
    },
]


@mcp.tool()
def list_namespaces() -> list[str]:
    """列出集群中的命名空间。"""
    return sorted(_PODS)


@mcp.tool()
def list_pods(namespace: str = "") -> list[dict]:
    """列出 Pod 及其状态/重启次数。namespace 留空则返回所有命名空间。"""
    if namespace:
        return _PODS.get(namespace, [])
    return [dict(pod, namespace=ns) for ns, pods in _PODS.items() for pod in pods]


@mcp.tool()
def recent_events(namespace: str = "") -> list[dict]:
    """列出最近的事件（含告警类事件，如 BackOff / Unhealthy）。"""
    if namespace:
        return [event for event in _EVENTS if event["namespace"] == namespace]
    return list(_EVENTS)


@mcp.tool()
def pod_logs(namespace: str, pod: str, tail: int = 20) -> str:
    """读取某个 Pod 的最近日志（演示数据，只读）。"""
    return (
        f"[demo] {namespace}/{pod} 最近 {tail} 行日志：\n"
        "2026-07-17 10:02:11 ERROR 连接 MySQL 3307 超时（connect timeout=5000ms）\n"
        "2026-07-17 10:02:12 WARN  重试 1/3 ...\n"
        "2026-07-17 10:02:18 ERROR 重试耗尽，任务进入失败队列\n"
    )


if __name__ == "__main__":
    mcp.run()  # 默认 stdio 传输

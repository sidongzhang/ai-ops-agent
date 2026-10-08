"""只读操作策略（后端侧入口）。

实现位于共享目录 `shared/ops_policy.py`（诊断 Agent 工具、MCP 服务、采集器共用），
这里注入共享路径后转发，避免各处重复拼路径。
"""
from ...core.shared_path import ensure_shared_path

ensure_shared_path()

from ops_policy import (  # noqa: E402
    KAFKA_DANGEROUS_FLAGS,
    KAFKA_READONLY_SUBCOMMANDS,
    REDIS_READONLY_SUBCOMMANDS,
    REDIS_READONLY_VERBS,
    kafka_command_denied,
    redis_command_denied,
)

__all__ = [
    "REDIS_READONLY_VERBS",
    "REDIS_READONLY_SUBCOMMANDS",
    "KAFKA_READONLY_SUBCOMMANDS",
    "KAFKA_DANGEROUS_FLAGS",
    "redis_command_denied",
    "kafka_command_denied",
]

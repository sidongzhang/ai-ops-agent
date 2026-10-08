"""容器状态探测（后端侧入口）。

实现位于共享目录 `shared/container_state.py`（Agent 工具、容器巡检、MCP 服务共用）。
"""
from ...core.shared_path import ensure_shared_path

ensure_shared_path()

from container_state import (  # noqa: E402
    INSPECT_FORMAT,
    format_container_state,
    inspect_container,
    state_signature,
)

__all__ = ["INSPECT_FORMAT", "inspect_container", "format_container_state", "state_signature"]

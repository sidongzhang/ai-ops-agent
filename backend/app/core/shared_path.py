"""共享组件目录（repo/shared）的 sys.path 注入。

`shared/` 下的模块被后端、采集器、打包产物共同引用（连接器 SDK、只读策略、容器探测），
此前 `runtime.py` 与各处 shim 各自拼一次路径；这里收敛为唯一入口。
"""
import os
import sys

from .config import settings


def ensure_shared_path() -> str:
    """把 repo/shared 加入 sys.path（幂等），返回该目录。"""
    shared = os.path.join(settings.repo_root, "shared")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    return shared


__all__ = ["ensure_shared_path"]

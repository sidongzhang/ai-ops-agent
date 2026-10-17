"""pytest 引导：把 backend/ 与 evals/ 加进 sys.path，并修掉会炸 httpx 的 NO_PROXY。

注意：仓库根的 `pyproject.toml` 不存在，`backend/pyproject.toml` 里的
`pythonpath = [".."]` 在 `pytest evals/` 时不会被加载，所以 sys.path 必须在这里自己处理。
"""
from __future__ import annotations

import sys
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVALS_DIR.parent

for _path in (str(REPO_ROOT / "backend"), str(EVALS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from harness import bootstrap  # noqa: E402

bootstrap()

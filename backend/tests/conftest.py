"""测试固定路径：让 `collector` 等仓库根目录下的包可导入。

测试文件在 backend/tests/ 下，pytest 默认只把 backend/ 放进 sys.path；
但被测代码会 import 仓库根目录的 `collector`（agentless 采集器）。
这里显式补上仓库根，避免"从哪个目录跑测试"影响结果。
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

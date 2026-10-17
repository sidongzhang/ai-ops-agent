"""测试固定路径：让 `collector` 等仓库根目录下的包可导入。

测试文件在 backend/tests/ 下，pytest 默认只把 backend/ 放进 sys.path；
但被测代码会 import 仓库根目录的 `collector`（agentless 采集器）。
这里显式补上仓库根，避免"从哪个目录跑测试"影响结果。
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# 测试不依赖真实密钥：OpenAI 客户端在**构造期**就要求 api_key，
# CI 没有 .env（LLM_API_KEY 为空）时会直接抛 OpenAIError。
# 这里给一个假值；env var 优先级高于 .env，本地有真实 key 时也不会覆盖。
os.environ.setdefault("LLM_API_KEY", "sk-test-not-real")

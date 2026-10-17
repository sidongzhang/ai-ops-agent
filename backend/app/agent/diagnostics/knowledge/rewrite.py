"""多轮问题改写（Query Rewrite）：把"那怎么办"补全成可检索的问题。

背景：多轮诊断里，追问往往只有几个字（"那怎么办""然后呢""这个怎么修"），
直接拿去做向量 / BM25 检索几乎必然跑偏——上一轮的实体（服务名、指标、组件）在追问里丢了。

策略：**规则化指代消解**（零延迟、可解释、可测）
  • 只在"问题很短"或"含指代/延续词"且**有上轮上下文**时触发；
  • 用上一轮问题做锚点拼进来，不改写原问题本身（原问题仍会完整交给模型）；
  • 任何异常都回退原问题——改写失败绝不能影响检索。

为什么不默认上 LLM 改写：多一次模型调用 = 多 1~2s 延迟 + 一次可能失败的依赖；
规则版覆盖了绝大多数追问形态。需要更强改写时再开 LLM 路径（见 settings 开关）。
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

SHORT_QUESTION = 12                       # ≤ 12 字视为"短追问"
ANAPHORA = ("那", "这个", "那个", "它", "他们", "然后", "继续", "还有", "怎么办", "为什么")
MAX_QUERY_CHARS = 160


def _previous_question(history: str) -> str:
    """从会话上下文里取上一轮问题（service 层注入的格式：`上一轮问题: ...`）。"""
    for line in (history or "").splitlines():
        match = re.match(r"\s*上一轮问题[:：]\s*(.+)", line)
        if match:
            return match.group(1).strip()
    return ""


def needs_rewrite(question: str) -> bool:
    text = (question or "").strip()
    if not text:
        return False
    if len(text) <= SHORT_QUESTION:
        return True
    return any(word in text for word in ANAPHORA)


def rewrite_query(
    question: str,
    history: str = "",
    *,
    enabled: bool = True,
    max_chars: int = MAX_QUERY_CHARS,
) -> str:
    """返回用于**检索**的 query；不改变交给模型的原始问题。"""
    text = (question or "").strip()
    if not enabled or not text:
        return text
    prev = _previous_question(history)
    if not prev or not needs_rewrite(text):
        return text
    if prev in text:          # 已经是完整问题，不重复拼
        return text
    merged = f"{prev} {text}".strip()
    return merged[:max_chars]

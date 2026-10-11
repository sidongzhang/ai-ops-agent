"""上下文投影 / 压缩：诊断历史变长时，缩短旧消息正文，控制发给模型的上下文规模。

对应 Pi 的 compaction 思想：**消息（日志）不删，只改变"发给模型的那份投影"**。
本实现刻意保守——**不增删消息、不拆散 tool_call / tool_return 配对**，只把"较早且过长"
的字符串内容（工具结果、文本）截断成短占位，最近若干条保持原样。

通过 pydantic-ai 的 `capabilities=[ProcessHistory(compact_messages)]` 挂载，
在每个模型请求前生效。
"""
import dataclasses
import json
import logging

log = logging.getLogger(__name__)

DEFAULT_BUDGET_CHARS = 60000     # 超过这个大致字符数才触发压缩
DEFAULT_KEEP_TAIL = 8            # 最近 N 条保持原样
DEFAULT_OLD_PART_CHARS = 400     # 更早的消息里，单个内容段保留的字符数
MARK = " …（早前内容已压缩）"


def _part_text(part) -> str:
    content = getattr(part, "content", None)
    if isinstance(content, str):
        return content
    args = getattr(part, "args", None)
    if isinstance(args, str):
        return args
    if isinstance(args, dict):
        try:
            return json.dumps(args, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            return ""
    return ""


def approx_chars(messages) -> int:
    total = 0
    for msg in messages or ():
        for part in getattr(msg, "parts", ()) or ():
            total += len(_part_text(part))
    return total


def _with(obj, **changes):
    """返回替换了字段的新对象（pydantic-ai 的消息/parts 是 dataclass，可变）。"""
    try:
        return dataclasses.replace(obj, **changes)
    except Exception:  # noqa: BLE001
        try:
            for key, value in changes.items():
                setattr(obj, key, value)
            return obj
        except Exception:  # noqa: BLE001
            return obj


def _truncate_part(part, limit: int):
    content = getattr(part, "content", None)
    if isinstance(content, str) and len(content) > limit:
        return _with(part, content=content[:limit] + MARK)
    args = getattr(part, "args", None)
    if isinstance(args, str) and len(args) > limit:
        return _with(part, args=args[:limit] + MARK)
    return part


def _truncate_message(msg, limit: int):
    parts = getattr(msg, "parts", None)
    if not parts:
        return msg
    try:
        return _with(msg, parts=[_truncate_part(p, limit) for p in parts])
    except Exception:  # noqa: BLE001
        return msg


def compact_messages(
    messages,
    *,
    budget: int = DEFAULT_BUDGET_CHARS,
    keep_tail: int = DEFAULT_KEEP_TAIL,
    old_chars: int = DEFAULT_OLD_PART_CHARS,
):
    """pydantic-ai history processor：超预算时压缩旧消息内容。

    输入/输出均为 list[ModelMessage]；结构不变，仅缩短较早消息的正文。
    """
    if not messages:
        return messages
    try:
        if approx_chars(messages) <= budget:
            return messages
        cutoff = max(0, len(messages) - keep_tail)
        compacted = [
            msg if i >= cutoff else _truncate_message(msg, old_chars)
            for i, msg in enumerate(messages)
        ]
        # 仍超预算：对更早的部分再激进压一次（保留最近 2 条原样）
        if approx_chars(compacted) > budget:
            tail_cut = max(0, len(compacted) - 2)
            compacted = [
                msg if i >= tail_cut else _truncate_message(msg, max(120, old_chars // 2))
                for i, msg in enumerate(compacted)
            ]
        return compacted
    except Exception as exc:  # noqa: BLE001 - 压缩失败绝不能影响诊断
        log.debug("[history] 压缩失败，按原样返回: %s", exc)
        return messages

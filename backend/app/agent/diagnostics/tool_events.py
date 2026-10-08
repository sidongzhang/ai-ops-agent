"""把 pydantic-ai 的工具事件流规范化为平台内部的 tool_start / tool_end 载荷。

主代理（runner）与取证子代理（tools.investigate）都要把工具事件翻译成同一种
简洁结构，此前两处各写了一份几乎相同的解析逻辑。这里统一为唯一实现：

- ``iter_tool_events``：把原始事件流翻译成规范化载荷；
- ``safe_emit``：进度回调失败不能影响诊断本身。

两种消费者共用同一数据源：
- 主代理：载荷直接转发给 ``on_progress``（前端实时工具链）；
- 子代理：载荷追加进 ``AgentDeps.nested_tool_calls``（审计/评分/轨迹），
  同时可选地转发给主代理的 ``progress_sink``。
"""
import logging
import time
from collections.abc import AsyncIterator, Callable

log = logging.getLogger(__name__)

# 子代理轨迹会落库，输出截断得比实时链路更狠，避免轨迹表膨胀。
NESTED_OUTPUT_LIMIT = 600
PROGRESS_OUTPUT_LIMIT = 1500


async def iter_tool_events(events, *, id_prefix: str = "") -> AsyncIterator[dict]:
    """规范化 pydantic-ai 工具事件：产出 tool_start / tool_end 两种载荷。

    ``id_prefix`` 用于给 call_id 加命名空间：子代理的事件与主代理共用同一个 call_id
    空间，加 ``sub-`` 前缀可避免两者在合并后的轨迹/实时工具链里相互覆盖。
    """
    from pydantic_ai.messages import FunctionToolCallEvent, FunctionToolResultEvent

    started: dict[str, float] = {}
    async for event in events:
        if isinstance(event, FunctionToolCallEvent):
            part = event.part
            call_id = f"{id_prefix}{part.tool_call_id or f'{part.tool_name}:{len(started)}'}"
            started[call_id] = time.monotonic()
            try:
                args = part.args_as_dict()
            except Exception:  # noqa: BLE001
                args = str(part.args or "")[:1000]
            yield {
                "kind": "tool_start",
                "call_id": call_id,
                "tool": part.tool_name,
                "input": args,
            }
        elif isinstance(event, FunctionToolResultEvent):
            part = event.part
            raw_id = getattr(part, "tool_call_id", "") or ""
            call_id = f"{id_prefix}{raw_id}"
            began = started.pop(call_id, None)
            outcome = getattr(part, "outcome", "success")
            output = getattr(part, "content", None) or event.content or ""
            yield {
                "kind": "tool_end",
                "call_id": call_id,
                "tool": getattr(part, "tool_name", "") or "",
                "status": "success" if outcome == "success" else str(outcome),
                "duration_ms": round((time.monotonic() - began) * 1000) if began else 0,
                "output": str(output)[:PROGRESS_OUTPUT_LIMIT],
            }


def safe_emit(sink: Callable[[dict], None] | None, payload: dict) -> None:
    """转发进度载荷；回调异常只记日志，绝不影响诊断主流程。"""
    if sink is None:
        return
    try:
        sink(payload)
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[diagnose] 进度回调失败（不影响诊断）: {exc}")


__all__ = ["iter_tool_events", "safe_emit", "NESTED_OUTPUT_LIMIT", "PROGRESS_OUTPUT_LIMIT"]

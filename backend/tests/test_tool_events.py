"""进度事件翻译器（tool_events）的回归测试。

主代理与取证子代理共用同一份工具事件翻译逻辑，这里钉住它的载荷契约，
避免两边再次各自实现后出现字段/截断/命名空间不一致。
"""
import asyncio
import unittest

from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ToolCallPart,
    ToolReturnPart,
)

from app.agent.diagnostics.tool_events import (
    NESTED_OUTPUT_LIMIT,
    iter_tool_events,
    safe_emit,
)


async def _aiter(items):
    for item in items:
        yield item


def _collect(events, **kwargs):
    async def run():
        return [payload async for payload in iter_tool_events(_aiter(events), **kwargs)]

    return asyncio.run(run())


class ToolEventTranslationTests(unittest.TestCase):
    def test_start_and_end_are_normalized(self) -> None:
        events = [
            FunctionToolCallEvent(
                ToolCallPart(tool_name="get_metrics", args={"system": 3}, tool_call_id="call_1")
            ),
            FunctionToolResultEvent(
                ToolReturnPart(tool_name="get_metrics", content="lag=5", tool_call_id="call_1")
            ),
        ]
        payloads = _collect(events)

        self.assertEqual([p["kind"] for p in payloads], ["tool_start", "tool_end"])
        start, end = payloads
        self.assertEqual(start["call_id"], "call_1")
        self.assertEqual(start["tool"], "get_metrics")
        self.assertEqual(start["input"], {"system": 3})
        self.assertEqual(end["call_id"], "call_1")
        self.assertEqual(end["status"], "success")
        self.assertEqual(end["output"], "lag=5")

    def test_id_prefix_namespaces_sub_agent_calls(self) -> None:
        events = [
            FunctionToolCallEvent(
                ToolCallPart(tool_name="check", args={}, tool_call_id="call_9")
            ),
            FunctionToolResultEvent(
                ToolReturnPart(tool_name="check", content="ok", tool_call_id="call_9")
            ),
        ]
        payloads = _collect(events, id_prefix="sub-")

        self.assertEqual([p["call_id"] for p in payloads], ["sub-call_9", "sub-call_9"])

    def test_end_without_start_has_zero_duration(self) -> None:
        events = [
            FunctionToolResultEvent(
                ToolReturnPart(tool_name="orphan", content="x", tool_call_id="call_x")
            )
        ]
        payloads = _collect(events)

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["kind"], "tool_end")
        self.assertEqual(payloads[0]["duration_ms"], 0)

    def test_output_is_truncated(self) -> None:
        events = [
            FunctionToolResultEvent(
                ToolReturnPart(tool_name="big", content="x" * 5000, tool_call_id="call_big")
            )
        ]
        payloads = _collect(events)

        self.assertLessEqual(len(payloads[0]["output"]), 1500)
        self.assertLessEqual(len(payloads[0]["output"]), NESTED_OUTPUT_LIMIT + 1000)

    def test_safe_emit_swallows_callback_errors(self) -> None:
        def boom(_payload):
            raise RuntimeError("sink down")

        safe_emit(boom, {"kind": "tool_start"})  # 不应抛出
        safe_emit(None, {"kind": "tool_start"})  # None 时静默返回


if __name__ == "__main__":
    unittest.main()

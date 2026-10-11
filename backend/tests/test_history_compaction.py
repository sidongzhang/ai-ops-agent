"""上下文压缩：超预算才压缩、只压旧消息、结构不变。"""
from pydantic_ai.messages import ModelRequest, ToolReturnPart

from app.agent.diagnostics.history import MARK, approx_chars, compact_messages


def _req(call_id: str, content: str) -> ModelRequest:
    return ModelRequest(parts=[ToolReturnPart(tool_name="t", content=content, tool_call_id=call_id)])


def test_under_budget_is_unchanged():
    msgs = [_req(f"c{i}", "x" * 100) for i in range(5)]
    out = compact_messages(msgs, budget=10_000)
    assert approx_chars(out) == approx_chars(msgs)


def test_truncates_old_keeps_tail_and_structure():
    long = "y" * 5000
    msgs = [_req(f"c{i}", long) for i in range(12)]
    out = compact_messages(msgs, budget=20_000, keep_tail=3, old_chars=200)

    assert len(out) == len(msgs)  # 条数/结构不变（不破坏 tool_call/return 配对）
    # 最近 3 条保持原样
    for msg in out[-3:]:
        assert approx_chars([msg]) == 5000
    # 更早的被截断
    assert approx_chars([out[0]]) < 5000
    assert MARK in out[0].parts[0].content


def test_never_raises_on_odd_input():
    assert compact_messages([]) == []
    # 非预期对象也不应抛异常
    compact_messages([object()], budget=1)

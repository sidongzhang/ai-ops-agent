"""执行层取消验收：模型请求中取消 / 工具执行中取消（离线，无网络）。

对应验收矩阵 ① ② ：
  * ① 模型请求中停止 → 抛 RunCancelled；带上已完成消息历史与用量；**不触发降级/备用模型**。
  * ② 工具执行中停止 → 已完成的工具结果保留在历史里；相位记为 tool_exec。
"""
import asyncio
import threading
import time
import unittest
from unittest.mock import patch

from pydantic_ai import CancellationToken
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from app.agent.diagnostics import runner

DESCRIPTOR = {"name": "demo", "id": "1", "services": []}
QUESTION = "系统健康吗？"


def _run_in_thread(fn, delay: float, token: CancellationToken, timeout: float = 25.0, gate=None):
    """在后台线程跑诊断；`gate` 事件就绪（或 delay 秒后）取消 token，模拟用户在另一线程点停止。"""
    holder: dict = {}

    def _worker():
        holder["result"] = fn()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    if gate is not None:
        gate.wait(timeout=15.0)          # 等真正进入目标阶段再取消（避免时序抖动）
    else:
        time.sleep(delay)
    token.cancel()
    thread.join(timeout)
    return holder.get("result"), thread.is_alive()


class CancelDuringModelRequestTests(unittest.TestCase):
    def test_cancel_during_model_request(self) -> None:
        """真实语义：在途那一轮照付，但**不再发下一轮**（省的是后续轮）。

        注意：传了 event_stream_handler 时框架走**流式**路径，因此这里用 stream_function。
        线上模型（deepseek/OpenAI 兼容）本身就是流式，与该路径一致。
        """
        original = runner.pick_model
        calls = {"n": 0}
        entered = threading.Event()

        async def stream_fn(messages, info):
            calls["n"] += 1
            entered.set()                      # 第一轮请求已真正发出（在途）
            for _ in range(20):                # 慢速流，给取消留出窗口
                await asyncio.sleep(0.4)
                yield "x"
            yield ModelResponse(parts=[ToolCallPart("list_services", {})])

        runner.pick_model = lambda *a, **k: FunctionModel(stream_function=stream_fn)
        token = CancellationToken()
        phases: list[str] = []
        try:
            result, still_alive = _run_in_thread(
                lambda: runner.diagnose_with_details(
                    # skill_steps="" 显式跳过"剧本语义路由"的 embedding 调用：
                    # 那是网络调用，机器繁忙时会让"首个模型请求"迟迟发不出，导致用例假失败。
                    DESCRIPTOR, QUESTION, cancel_token=token, on_phase=phases.append,
                    skill_steps="",
                ),
                delay=0,
                token=token,
                gate=entered,
            )
        finally:
            runner.pick_model = original

        self.assertTrue(entered.is_set(), "模型请求应已发出，取消才发生在'请求在途'")
        self.assertFalse(still_alive, "取消后运行线程应及时退出")
        self.assertIsNotNone(result, "取消必须返回可恢复结果，而不是抛异常给上层")
        self.assertTrue(result.cancelled, "在途请求应被中断（不再进入下一轮）")
        self.assertEqual(result.answer, "", "取消不应产生答案")
        self.assertTrue(result.messages_json, "取消时必须带上已完成的消息历史")
        self.assertIn("model_request", phases, "取消前应处于模型请求相位")
        self.assertEqual(calls["n"], 1, "取消后不得再次发起模型请求")


class CancelDuringToolExecutionTests(unittest.TestCase):
    def test_cancel_during_tool_execution(self) -> None:
        original = runner.pick_model
        runner.pick_model = lambda *a, **k: TestModel(call_tools="all")   # 触发真实工具链
        token = CancellationToken()
        phases: list[str] = []
        slow_tool = patch(
            "app.services.descriptors.health.collect_health",
            side_effect=lambda *a, **k: (time.sleep(6) or []),
        )
        try:
            with slow_tool:
                result, still_alive = _run_in_thread(
                    lambda: runner.diagnose_with_details(
                        DESCRIPTOR, QUESTION, cancel_token=token, on_phase=phases.append,
                        skill_steps="",
                    ),
                    delay=1.5,
                    token=token,
                )
        finally:
            runner.pick_model = original

        self.assertFalse(still_alive)
        self.assertIsNotNone(result)
        self.assertTrue(result.cancelled)
        self.assertTrue(result.messages_json)
        self.assertIn("tool_exec", phases, "取消落在工具执行期，相位应记为 tool_exec")


if __name__ == "__main__":
    unittest.main()

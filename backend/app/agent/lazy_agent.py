"""延迟构造 Agent 的轻量代理。

为什么需要：`Agent(default_model(), ...)` 在**构造时**就会建立 OpenAI 兼容客户端，
没有凭证（CI、纯离线评测、只做静态导入的脚本）会在 **import 阶段**直接抛
`openai.OpenAIError: Missing credentials`。把构造推迟到首次真正调用，
import 就不再依赖任何 LLM 凭证。

用法：
    agent = LazyAgent(lambda: Agent(default_model(), deps_type=Deps))
    agent.run_sync(...)      # 首次访问时才真正构造
"""
from __future__ import annotations

from typing import Any, Callable


class LazyAgent:
    def __init__(self, factory: Callable[[], Any], *, name: str = "agent") -> None:
        self._factory = factory
        self._instance: Any = None
        self._name = name

    def _get(self) -> Any:
        if self._instance is None:
            self._instance = self._factory()
        return self._instance

    def __getattr__(self, item: str) -> Any:
        # 仅在常规属性查找失败时触发（_factory/_instance/_name 是实例字段，不会走到这里）
        return getattr(self._get(), item)

    def __repr__(self) -> str:  # pragma: no cover - 调试友好
        state = "built" if self._instance is not None else "lazy"
        return f"<LazyAgent {self._name} {state}>"

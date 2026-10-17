"""模型降级策略：把"选谁 / 能不能降 / 降到哪"从 runner 的嵌套分支里抽出来。

三条产品约束（都有测试锁住）：
1. **显式选择不得静默换模型**：用户选 local 常常是为了数据不出内网，
   本地失败就偷偷调远程 API 等于把数据发出去；只允许用同一个模型做纯文本重试。
2. **带图请求不得降级到纯文本**：丢图比失败更糟。
3. **auto 模式**：当前默认是本地模型且配置了 API key 时，可切远程 API 重跑。

降级目标只有两种：换 API 模型重跑完整 agent，或同模型做一次纯文本兜底。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.agent.llm import default_endpoint, endpoint_for_mode, make_chat_model

# 用户/接口显式指定的模式：绝不换模型（只允许同模型文本重试）
EXPLICIT_MODES = {"local", "api", "advanced", "default"}


class FallbackMode(str, Enum):
    NONE = "none"            # 不允许降级（带图 / 需要显式失败）
    API_MODEL = "api_model"  # 切远程 API 模型重跑
    TEXT = "text"            # 同一模型做纯文本兜底


@dataclass(frozen=True)
class FallbackDecision:
    mode: FallbackMode
    model: object | None = None
    reason: str = ""


def api_fallback_model(model_mode: str):
    """auto 模式下可用的 API 模型；显式选择或无可用 key 时返回 None。"""
    if (model_mode or "auto").strip().lower() in EXPLICIT_MODES:
        return None
    if default_endpoint().mode != "local":
        return None
    api_endpoint = endpoint_for_mode("api")
    if not api_endpoint.api_key:
        return None
    return make_chat_model(api_endpoint)


def decide_fallback(*, model_mode: str, has_images: bool) -> FallbackDecision:
    if has_images:
        return FallbackDecision(FallbackMode.NONE, reason="image_input_requires_multimodal")
    model = api_fallback_model(model_mode)
    if model is not None:
        return FallbackDecision(FallbackMode.API_MODEL, model=model, reason="local_model_failed")
    return FallbackDecision(FallbackMode.TEXT, reason="primary_model_failed")

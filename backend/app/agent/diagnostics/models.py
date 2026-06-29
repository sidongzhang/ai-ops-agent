"""Model routing for diagnosis agent."""
import logging

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from ...core.config import settings

log = logging.getLogger(__name__)

ADVANCED_KEYWORDS = {
    "P0", "p0", "崩溃", "宕机", "数据丢失", "根因分析",
    "紧急", "生产故障", "无法访问", "大面积", "所有服务",
}


def make_model(model_name: str, base_url: str, api_key: str) -> OpenAIChatModel:
    return OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(base_url=base_url, api_key=api_key),
    )


def default_model() -> OpenAIChatModel:
    return make_model(
        settings.agent_model,
        settings.deepseek_base_url,
        settings.deepseek_api_key,
    )


def advanced_model() -> OpenAIChatModel:
    return make_model(
        settings.advanced_agent_model,
        settings.advanced_agent_base_url or settings.deepseek_base_url,
        settings.advanced_agent_api_key or settings.deepseek_api_key,
    )


def pick_model(question: str) -> OpenAIChatModel:
    if settings.advanced_agent_model and any(keyword in question for keyword in ADVANCED_KEYWORDS):
        log.info(f"[model-routing] 升档至高级模型 {settings.advanced_agent_model!r}")
        return advanced_model()
    return default_model()

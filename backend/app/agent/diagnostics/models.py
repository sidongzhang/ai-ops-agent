"""Model routing for diagnosis agent."""
import logging

from pydantic_ai.models.openai import OpenAIChatModel

from app.agent.llm import advanced_endpoint, default_endpoint, endpoint_for_choice, make_chat_model
from ...core.config import settings

log = logging.getLogger(__name__)

ADVANCED_KEYWORDS = {
    "P0", "p0", "崩溃", "宕机", "数据丢失", "根因分析",
    "紧急", "生产故障", "无法访问", "大面积", "所有服务",
}


def default_model() -> OpenAIChatModel:
    return make_chat_model(default_endpoint())


def advanced_model() -> OpenAIChatModel:
    return make_chat_model(advanced_endpoint())


def pick_model(question: str, model_mode: str = "auto", model_name: str = "") -> OpenAIChatModel:
    selected_endpoint = endpoint_for_choice(model_mode, model_name)
    if selected_endpoint:
        log.info(
            "[model-routing] 使用用户选择模型 mode=%s model=%r",
            selected_endpoint.mode,
            selected_endpoint.model,
        )
        return make_chat_model(selected_endpoint)
    if settings.advanced_agent_model and any(keyword in question for keyword in ADVANCED_KEYWORDS):
        log.info(f"[model-routing] 升档至高级模型 {settings.advanced_agent_model!r}")
        return advanced_model()
    return default_model()

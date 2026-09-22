"""Unified OpenAI-compatible LLM configuration.

Supports both remote APIs and local deployments such as Ollama, vLLM, and
LM Studio, as long as they expose OpenAI-compatible endpoints.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.core.config import settings


@dataclass(frozen=True)
class LLMEndpoint:
    mode: str
    model: str
    base_url: str
    api_key: str


def _clean_mode(value: str) -> str:
    mode = (value or "api").strip().lower()
    return "local" if mode == "local" else "api"


def default_endpoint() -> LLMEndpoint:
    return endpoint_for_mode(settings.llm_mode)


def advanced_endpoint() -> LLMEndpoint:
    mode = _clean_mode(settings.advanced_llm_mode or settings.llm_mode)
    if mode == "local":
        return LLMEndpoint(
            mode="local",
            model=(
                settings.advanced_llm_local_model
                or settings.llm_local_model
                or settings.agent_model
            ),
            base_url=settings.advanced_llm_local_base_url or settings.llm_local_base_url,
            api_key=settings.advanced_llm_local_api_key or settings.llm_local_api_key or "ollama",
        )
    return LLMEndpoint(
        mode="api",
        model=(
            settings.advanced_llm_api_model
            or settings.advanced_agent_model
            or settings.llm_api_model
            or settings.agent_model
        ),
        base_url=(
            settings.advanced_llm_api_base_url
            or settings.advanced_agent_base_url
            or settings.llm_api_base_url
            or settings.deepseek_base_url
        ),
        api_key=(
            settings.advanced_llm_api_key
            or settings.advanced_agent_api_key
            or settings.llm_api_key
            or settings.deepseek_api_key
        ),
    )


def endpoint_for_choice(choice: str = "auto", model_name: str = "") -> LLMEndpoint | None:
    """Resolve an optional per-request model choice.

    None means "auto", letting the diagnosis router pick default or advanced
    according to the question severity.
    """
    selected = (choice or "auto").strip().lower()
    if selected in {"", "auto"}:
        return None
    if selected == "advanced":
        endpoint = advanced_endpoint()
    elif selected == "local":
        endpoint = endpoint_for_mode("local")
    elif selected in {"api", "default"}:
        endpoint = endpoint_for_mode("api" if selected == "api" else settings.llm_mode)
    else:
        return None

    override_model = (model_name or "").strip()
    if override_model:
        return LLMEndpoint(
            mode=endpoint.mode,
            model=override_model,
            base_url=endpoint.base_url,
            api_key=endpoint.api_key,
        )
    return endpoint


def model_options() -> list[dict]:
    """Safe model options for the UI. Never expose API keys."""
    default = default_endpoint()
    api = endpoint_for_mode("api")
    local = endpoint_for_mode("local")
    advanced = advanced_endpoint()
    return [
        {
            "value": "auto",
            "label": "自动",
            "description": "按问题严重程度自动选择默认或高级模型",
            "mode": default.mode,
            "model": default.model,
        },
        {
            "value": "local",
            "label": "本地模型",
            "description": "走本机 Ollama/vLLM/LM Studio 等本地 OpenAI 兼容服务",
            "mode": local.mode,
            "model": local.model,
        },
        {
            "value": "api",
            "label": "API 模型",
            "description": "走远程 OpenAI 兼容 API，适合正式诊断",
            "mode": api.mode,
            "model": api.model,
        },
        {
            "value": "advanced",
            "label": "高级模型",
            "description": "用于 P0、宕机、根因分析等复杂问题",
            "mode": advanced.mode,
            "model": advanced.model,
        },
    ]


def endpoint_for_mode(mode_value: str) -> LLMEndpoint:
    mode = _clean_mode(mode_value)
    if mode == "local":
        return LLMEndpoint(
            mode="local",
            model=settings.llm_local_model or settings.agent_model,
            base_url=settings.llm_local_base_url,
            api_key=settings.llm_local_api_key or "ollama",
        )
    return LLMEndpoint(
        mode="api",
        model=settings.llm_api_model or settings.agent_model,
        base_url=settings.llm_api_base_url or settings.deepseek_base_url,
        api_key=settings.llm_api_key or settings.deepseek_api_key,
    )


def make_chat_model(endpoint: LLMEndpoint | None = None) -> OpenAIChatModel:
    target = endpoint or default_endpoint()
    return OpenAIChatModel(
        target.model,
        provider=OpenAIProvider(base_url=target.base_url, api_key=target.api_key),
    )


def embedding_endpoint() -> LLMEndpoint:
    """Embedding endpoint. Empty embedding settings reuse the active LLM endpoint."""
    chat_endpoint = default_endpoint()
    return LLMEndpoint(
        mode=chat_endpoint.mode,
        model=settings.embedding_model,
        base_url=settings.embedding_base_url or chat_endpoint.base_url,
        api_key=settings.embedding_api_key or chat_endpoint.api_key,
    )

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


class LocalModelUnavailable(RuntimeError):
    """本地模型端点未就绪（连不上、或配置的模型没装）。

    为什么要前置检查：本地模型（尤其 7B）**加载失败/放不下内存**时，表现不是"立刻报错"，
    而是长时间"卡在 prefill"（实测 >8 分钟 0 token，最后被 OOM kill）。用户会以为是平台坏了。
    所以选 local 时先跑一次廉价就绪检查，就绪不了就立刻给明确原因。
    """


def local_model_status(
    endpoint: LLMEndpoint,
    *,
    timeout: float = 3.0,
    client=None,
) -> str | None:
    """检查本地端点是否可用。返回 None 表示可用，否则返回给用户看的原因。"""
    if endpoint.mode != "local":
        return None
    import httpx

    url = endpoint.base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {endpoint.api_key or 'ollama'}"}
    owned = client is None
    try:
        http = client or httpx.Client(timeout=timeout)
    except Exception as exc:  # pragma: no cover - 构造客户端失败极少见
        return f"无法创建 HTTP 客户端：{exc}"
    try:
        resp = http.get(url, headers=headers)
    except Exception as exc:
        return (
            f"连不上本地模型服务 {endpoint.base_url}（{type(exc).__name__}）。"
            "请确认 Ollama/vLLM/LM Studio 已启动，或改用「API 模型」"
        )
    finally:
        if owned:
            http.close()
    if resp.status_code != 200:
        return f"本地模型服务返回 HTTP {resp.status_code}（{url}）"
    try:
        entries = (resp.json() or {}).get("data") or []
        names = [str(item.get("id") or "") for item in entries if isinstance(item, dict)]
    except Exception:
        names = []
    if names and endpoint.model not in names:
        listing = "、".join(n for n in names if n) or "（空）"
        return (
            f"本地模型 {endpoint.model!r} 未安装（当前可用：{listing}）。"
            f"请先执行 `ollama pull {endpoint.model}`"
        )
    return None


def ensure_model_ready(
    model_mode: str,
    model_name: str = "",
    *,
    timeout: float = 3.0,
    client=None,
) -> LLMEndpoint | None:
    """解析用户选择的模型；若选了本地模型且没就绪，抛 LocalModelUnavailable。

    返回解析出的端点（None = auto，交给路由按问题严重程度决定）。
    """
    endpoint = endpoint_for_choice(model_mode, model_name)
    if endpoint is None:
        return None
    reason = local_model_status(endpoint, timeout=timeout, client=client)
    if reason:
        raise LocalModelUnavailable(f"本地模型未就绪：{reason}")
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
    if target.mode == "local":
        # 本地模型最常见的故障是"卡死"（内存不足被 OOM、或极慢的 CPU prefill）：
        # 没有超时会一直挂到用户手动停止（实测 >8 分钟 0 输出）。
        # 给一个**读超时**（默认 120s，可用 LLM_LOCAL_TIMEOUT_SECONDS 调），
        # 让"卡住"变成一条可读的失败信息。
        import httpx
        from openai import AsyncOpenAI

        seconds = max(5.0, float(settings.llm_local_timeout_seconds))
        client = AsyncOpenAI(
            base_url=target.base_url,
            api_key=target.api_key or "ollama",
            timeout=httpx.Timeout(connect=5.0, read=seconds, write=30.0, pool=5.0),
            max_retries=1,
        )
        return OpenAIChatModel(target.model, provider=OpenAIProvider(openai_client=client))
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

"""检索重排（strong rerank）：对召回候选重新排序，把最相关的排到最前。

两条路径（按 settings.rerank_mode 选择）：
  1) **rerank API**：配置了 settings.rerank_base_url 时，调用通用 rerank 接口
     （Cohere/Jina/SiliconFlow 风格：POST …/rerank {model,query,documents} → results[{index,relevance_score}]）。
  2) **LLM 重排**：未配 API 时，用现有 chat 模型（OpenAI 兼容 /chat/completions）
     给每个候选打 0-10 相关度，按分排序。

任何异常一律**按原顺序返回**，绝不阻断检索；候选太少（<=1）或 mode=off 时直接跳过。
"""
import json
import logging
import re

from app.core.config import settings
from ...harness.performance import measured

log = logging.getLogger(__name__)

MAX_CANDIDATES = 6
_JSON_ARRAY = re.compile(r"\[.*\]", re.DOTALL)


def _timeout() -> float:
    try:
        return max(2.0, float(settings.rerank_timeout_seconds or 8.0))
    except (TypeError, ValueError):
        return 8.0


@measured('preparation', 'rerank_api')
def _rerank_via_api(query: str, docs: list[dict]) -> list[dict] | None:
    import httpx

    url = settings.rerank_base_url.rstrip("/")
    if not url.endswith("/rerank"):
        url += "/rerank"
    headers = {"Content-Type": "application/json"}
    if settings.rerank_api_key:
        headers["Authorization"] = f"Bearer {settings.rerank_api_key}"
    payload = {
        "model": settings.rerank_model or "rerank",
        "query": query,
        "documents": [d.get("snippet", "") for d in docs],
        "top_n": len(docs),
    }
    with httpx.Client(timeout=_timeout()) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results") or data.get("data") or []
    scored: list[tuple[float, dict]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        idx = item.get("index", item.get("document_index"))
        score = item.get("relevance_score", item.get("score", 0))
        if isinstance(idx, int) and 0 <= idx < len(docs):
            try:
                scored.append((float(score), docs[idx]))
            except (TypeError, ValueError):
                continue
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [doc for _score, doc in scored]


def _parse_scores(text: str, count: int) -> dict[int, float] | None:
    match = _JSON_ARRAY.search(text or "")
    if not match:
        return None
    try:
        arr = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    scores: dict[int, float] = {}
    for item in arr if isinstance(arr, list) else []:
        if not isinstance(item, dict):
            continue
        idx = item.get("i", item.get("index"))
        value = item.get("s", item.get("score"))
        if isinstance(idx, int) and 0 <= idx < count and isinstance(value, (int, float)):
            scores[idx] = float(value)
    return scores or None


@measured('preparation', 'rerank_model')
def _rerank_via_llm(query: str, docs: list[dict]) -> list[dict] | None:
    import httpx

    from app.agent.llm import default_endpoint

    endpoint = default_endpoint()
    if not endpoint.base_url or not endpoint.api_key:
        return None
    listing = "\n".join(
        f"{i}. {(d.get('snippet') or '')[:200]}" for i, d in enumerate(docs)
    )
    prompt = (
        "你是检索重排器。根据用户问题，评估每个候选片段的相关度（0-10，越高越相关）。\n"
        f"问题：{query}\n候选：\n{listing}\n"
        '只输出 JSON 数组，如 [{"i":0,"s":8},{"i":1,"s":3}]，不要任何解释。'
    )
    with httpx.Client(timeout=_timeout()) as client:
        resp = client.post(
            f"{endpoint.base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {endpoint.api_key}", "Content-Type": "application/json"},
            json={
                "model": endpoint.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]

    scores = _parse_scores(content, len(docs))
    if not scores:
        return None
    order = sorted(range(len(docs)), key=lambda i: scores.get(i, 0.0), reverse=True)
    return [docs[i] for i in order]


def rerank_documents(query: str, docs: list[dict]) -> list[dict]:
    """对候选重排；失败/关闭时按原顺序返回。"""
    mode = (settings.rerank_mode or "auto").strip().lower()
    if mode == "off" or len(docs) <= 1:
        return docs
    docs = docs[:MAX_CANDIDATES]
    try:
        if mode in ("auto", "api") and settings.rerank_base_url:
            ordered = _rerank_via_api(query, docs)
            if ordered:
                return ordered
            if mode == "api":
                return docs
        if mode in ("auto", "llm"):
            ordered = _rerank_via_llm(query, docs)
            if ordered:
                return ordered
    except Exception as exc:  # noqa: BLE001 - 重排失败绝不能影响检索
        log.warning("[rerank] 重排失败，按原顺序返回: %s", exc)
    return docs

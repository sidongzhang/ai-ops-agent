"""LLM-as-judge：对答案做 faithfulness / correctness 打分（0~1）。

为什么需要：规则指标（证据词命中、required_tools、幻觉诱饵）只能测"有没有做动作"，
测不出"结论是否被证据支撑、是否与根因一致"。裁判只读**本题材料**（问题 / 参考答案 /
被测答案 / 工具摘要），不给它工具、不重跑被测模型；单题失败返回 None，不影响整批。

用法：`python evals/run_eval.py --run --judge`（会产生额外模型调用费用）。
"""
from __future__ import annotations

import json
import logging
import re

import httpx

log = logging.getLogger(__name__)

_PROMPT = """你是严格但公正的评测裁判。只依据下面的材料评分，不要臆测或补充材料外的事实。

【问题】{question}
【参考答案（根因）】{root_cause}
【被测答案】{answer}
【工具调用摘要】{evidence}

请只输出一个 JSON 对象：
{{"faithfulness": <0~1>, "correctness": <0~1>, "reason": "<不超过 60 字>"}}

评分口径：
- faithfulness：答案中的每个事实/数字是否都能在工具摘要里找到依据；无依据的编造按比例扣分。
- correctness：结论是否与参考答案的根因一致（表述不同不算错）。
"""


def _evidence_digest(record: dict, *, limit: int = 20, chars: int = 200, total_chars: int = 6000) -> str:
    """给裁判看**全部**工具摘要（截断到总预算），否则后面调用支撑的事会被误判为编造。"""
    lines: list[str] = []
    used = 0
    for call in (record.get("tool_calls") or [])[:limit]:
        output = str(call.get("output") or "")[:chars].replace("\n", " ")
        line = f"- {call.get('tool')}: {output}"
        if used + len(line) > total_chars:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines) or "（本轮没有工具调用）"


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:  # noqa: BLE001 - 裁判输出不可控，解析失败按 None 处理
        return {}


def _as_score(value) -> float | None:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def judge_case(case: dict, record: dict, *, timeout: float = 45.0) -> dict:
    """对单条（用例, 运行记录）打分；任何失败都返回 None 分数，绝不抛异常。"""
    answer = str(record.get("answer") or "")
    if not answer.strip():
        return {
            "judge_faithfulness": None,
            "judge_correctness": None,
            "judge_reason": "empty_answer",
        }

    from app.agent.llm import endpoint_for_mode

    endpoint = endpoint_for_mode("api")
    ground_truth = case.get("ground_truth") or {}
    prompt = _PROMPT.format(
        question=case.get("question", ""),
        root_cause=ground_truth.get("root_cause", ""),
        answer=answer[:4000],
        evidence=_evidence_digest(record),
    )
    try:
        response = httpx.post(
            f"{endpoint.base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {endpoint.api_key}"},
            json={
                "model": endpoint.model,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001 - 裁判不可用不能拖垮评测
        log.warning("[judge] 调用失败 case=%s: %s", case.get("id"), exc)
        return {
            "judge_faithfulness": None,
            "judge_correctness": None,
            "judge_reason": f"judge_error:{type(exc).__name__}",
        }

    data = _parse_json(content)
    return {
        "judge_faithfulness": _as_score(data.get("faithfulness")),
        "judge_correctness": _as_score(data.get("correctness")),
        "judge_reason": str(data.get("reason") or "")[:120],
    }

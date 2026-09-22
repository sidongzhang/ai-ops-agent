"""离线评测指标：纯函数，无第三方依赖，便于单元测试。

口径对齐 backend/app/services/analytics.py 的线上指标（见 README 的「指标定义」）：
  * 所有 `*_rate` 一律是百分比（0-100，保留 1 位小数），与 analytics 的 `_rate()` 一致；
  * `rca_accuracy` / `tool_selection_*` 是 0-1 的比例，保留 3 位小数。
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Sequence

#: 工具输出里出现这些标记说明该调用其实是「降级/失败」的（例如离线环境补不上 Docker）。
#: 这是启发式近似：tool_call.status 只反映工具函数是否抛异常，不反映命令本身是否成功。
DEGRADED_MARKERS: tuple[str, ...] = (
    "离线评测不支持",
    "远程 Kafka 查询失败",
    "远程 Redis 查询失败",
    "远程 Prometheus 查询失败",
    "查询失败",
    "执行失败",
    "命令超时",
    "未找到 Kafka 容器",
    "不可达",
    "未配置",
    "无法查询",
)

_SEPARATOR = re.compile(r"[\s_\-/.{}()\[\]:：,，、=]+")


def normalize_text(text: Any) -> str:
    """小写 + 分隔符归一（`used_memory` / `used memory` / `used-memory` 视为同一写法）。"""
    return _SEPARATOR.sub(" ", str(text or "").lower()).strip()


def key_match_weight(answer: str, key: str) -> float:
    """单个 evidence_key 的命中权重：1.0 全命中 / 0.5 部分命中 / 0.0 未命中。

    部分命中的定义（只对多词 key 生效，避免单 token 子串误判）：
      key 归一后切出的 token 里有 ≥50% 出现在答案里，例如 `evicted_keys`
      → 答案只写了 `evicted`，记 0.5 分。
    """
    key_norm = normalize_text(key)
    if not key_norm:
        return 0.0
    answer_norm = normalize_text(answer)
    if not answer_norm:
        return 0.0
    if key_norm in answer_norm:
        return 1.0
    tokens = key_norm.split()
    if len(tokens) >= 2:
        hit = sum(1 for token in tokens if token in answer_norm)
        if hit / len(tokens) >= 0.5:
            return 0.5
    return 0.0


def evidence_hits(answer: str, evidence_keys: Sequence[str]) -> list[dict]:
    """逐 key 的命中明细，供轨迹/报告审计。"""
    return [
        {"key": key, "weight": key_match_weight(answer, key)}
        for key in evidence_keys
    ]


def rca_accuracy(answer: str, evidence_keys: Sequence[str]) -> float:
    """答案对 ground_truth 证据词的加权覆盖率（部分命中记 0.5）。"""
    if not evidence_keys:
        return 0.0
    total = sum(item["weight"] for item in evidence_hits(answer, evidence_keys))
    return round(total / len(evidence_keys), 3)


def tool_selection_scores(required: Sequence[str], actual: Sequence[str]) -> dict:
    """以 required_tools 为正类的多标签 precision / recall / F1。"""
    required_set = {tool for tool in required if tool}
    actual_set = {tool for tool in actual if tool}
    true_positive = len(required_set & actual_set)
    precision = true_positive / len(actual_set) if actual_set else 0.0
    recall = true_positive / len(required_set) if required_set else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "tool_precision": round(precision, 3),
        "tool_recall": round(recall, 3),
        "tool_f1": round(f1, 3),
        "required_tool_hit": bool(true_positive),
        "required_tools_hit_count": true_positive,
        "required_tools_total": len(required_set),
        "actual_tools": sorted(actual_set),
        "unexpected_tools": sorted(actual_set - required_set),
        "missed_tools": sorted(required_set - actual_set),
    }


def call_signature(tool_call: dict) -> str:
    payload = tool_call.get("input", {})
    try:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        encoded = str(payload)
    return f"{tool_call.get('tool', '')}|{encoded}"


def redundant_call_rate(tool_calls: Sequence[dict]) -> float:
    """同一工具 + 同一入参重复调用的占比（0-1）。"""
    total = len(tool_calls)
    if not total:
        return 0.0
    unique = len({call_signature(call) for call in tool_calls})
    return round((total - unique) / total, 3)


def tool_param_validity(tool_calls: Sequence[dict]) -> float:
    """status == success 的 tool_call 占比（0-1）。无调用时记 0。"""
    total = len(tool_calls)
    if not total:
        return 0.0
    succeeded = sum(1 for call in tool_calls if call.get("status") == "success")
    return round(succeeded / total, 3)


def degraded_call_rate(tool_calls: Sequence[dict]) -> float:
    """工具调用被环境降级（依赖 Docker/网络而不可用）的占比（0-1）。"""
    total = len(tool_calls)
    if not total:
        return 0.0
    degraded = 0
    for call in tool_calls:
        output = str(call.get("output", ""))
        if any(marker in output for marker in DEGRADED_MARKERS):
            degraded += 1
    return round(degraded / total, 3)


_NEGATION_PREFIXES = ("不是", "并非", "没有", "排除", "非", "排除是", "别怪", "与…无关", "与...无关", "无关")


def hallucination_hits(answer: str, forbidden_conclusion: Sequence[str]) -> list[str]:
    """答案里出现的错误归因（归一后子串匹配）。

    否定语境不算命中：答案里「不是前端渲染问题」是在排除该归因，
    不应记为幻觉。规则：短语前 12 字内出现否定词则跳过该命中。
    """
    answer_norm = normalize_text(answer)
    hits = []
    for phrase in forbidden_conclusion or []:
        phrase_norm = normalize_text(phrase)
        if not phrase_norm or phrase_norm not in answer_norm:
            continue
        pos = answer_norm.find(phrase_norm)
        window = answer_norm[max(0, pos - 12): pos]
        if any(neg in window for neg in _NEGATION_PREFIXES):
            continue
        hits.append(phrase)
    return hits


def evidence_coverage(tool_calls: Sequence[dict]) -> float:
    """至少调用过 1 个工具才下结论记 1，否则 0（对应 diagnosis_evidence_rate_pct）。"""
    return 1.0 if len(tool_calls) >= 1 else 0.0


def score_case(case: dict, record: dict) -> dict:
    """把一条 (用例, 运行结果) 打分，返回扁平 dict（供报告与单元测试使用）。"""
    ground_truth = case.get("ground_truth", {}) or {}
    evidence_keys = list(ground_truth.get("evidence_keys", []) or [])
    required_tools = list(ground_truth.get("required_tools", []) or [])
    forbidden = list(ground_truth.get("forbidden_conclusion", []) or [])

    answer = str(record.get("answer") or "")
    tool_calls = list(record.get("tool_calls") or [])
    events = list(record.get("events") or [])
    error = record.get("error")
    actual_tools = [call.get("tool", "") for call in tool_calls]

    hits = evidence_hits(answer, evidence_keys)
    hit_count = sum(1 for item in hits if item["weight"] >= 1.0)
    forbidden_hits = hallucination_hits(answer, forbidden)
    tool_scores = tool_selection_scores(required_tools, actual_tools)

    # task_success：命中 required_tools（≥1）且答案含 ≥1 个 evidence_key 且无 forbidden_conclusion
    success = int(
        not error
        and tool_scores["required_tool_hit"]
        and hit_count >= 1
        and not forbidden_hits
    )
    weights_sum = sum(item["weight"] for item in hits)
    row = {
        "case_id": case.get("id", ""),
        "fault_type": case.get("fault_type", ""),
        "difficulty": case.get("difficulty", ""),
        "ablation": record.get("ablation", ""),
        "model": record.get("model", ""),
        "status": "error" if error else "ok",
        "error": str(error) if error else "",
        "task_success": success,
        "rca_accuracy": round(weights_sum / len(evidence_keys), 3) if evidence_keys else 0.0,
        "evidence_hit_count": hit_count,
        "evidence_partial_count": sum(1 for item in hits if 0 < item["weight"] < 1.0),
        "evidence_total_keys": len(evidence_keys),
        "evidence_hits": hits,
        "hallucination": 1 if forbidden_hits else 0,
        "hallucination_phrases": forbidden_hits,
        "evidence_coverage": evidence_coverage(tool_calls),
        "tool_call_count": len(tool_calls),
        "progress_event_count": len(events),
        "progress_tool_ms": sum(int(event.get("duration_ms") or 0) for event in events if event.get("kind") == "tool_end"),
        "tool_param_validity": tool_param_validity(tool_calls),
        "redundant_call_rate": redundant_call_rate(tool_calls),
        "degraded_call_rate": degraded_call_rate(tool_calls),
        "duration_ms": int(record.get("duration_ms") or 0),
        "total_tokens": int(record.get("total_tokens") or 0),
    }
    row.update(tool_scores)
    return row


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _pct(values: Iterable[float]) -> float:
    """与 analytics._rate 一致：0-100，保留 1 位小数。"""
    values = list(values)
    if not values:
        return 0.0
    return round(sum(values) / len(values) * 100, 1)


def aggregate(rows: Sequence[dict]) -> dict:
    """把若干 case 打分聚合成一组指标（宏平均）。"""
    rows = list(rows)
    errors = [row for row in rows if row.get("status") == "error"]
    metrics = {
        "cases": len(rows),
        "errors": len(errors),
        "task_success_rate": _pct(row.get("task_success", 0) for row in rows),
        "rca_accuracy": round(_mean(row.get("rca_accuracy", 0.0) for row in rows), 3),
        "tool_selection_f1": round(_mean(row.get("tool_f1", 0.0) for row in rows), 3),
        "tool_selection_precision": round(_mean(row.get("tool_precision", 0.0) for row in rows), 3),
        "tool_selection_recall": round(_mean(row.get("tool_recall", 0.0) for row in rows), 3),
        "tool_param_validity": _pct(row.get("tool_param_validity", 0.0) for row in rows),
        "evidence_coverage_rate": _pct(row.get("evidence_coverage", 0.0) for row in rows),
        "avg_tool_calls": round(_mean(row.get("tool_call_count", 0) for row in rows), 2),
        "avg_progress_events": round(_mean(row.get("progress_event_count", 0) for row in rows), 2),
        "redundant_call_rate": _pct(row.get("redundant_call_rate", 0.0) for row in rows),
        "hallucination_rate": _pct(row.get("hallucination", 0) for row in rows),
        "tool_degraded_rate": _pct(row.get("degraded_call_rate", 0.0) for row in rows),
        "avg_duration_ms": round(_mean(row.get("duration_ms", 0) for row in rows), 1),
        "avg_total_tokens": round(_mean(row.get("total_tokens", 0) for row in rows), 1),
    }
    # 与线上 analytics 口径的显式别名（便于跨表对比）
    metrics["diagnosis_evidence_rate_pct"] = metrics["evidence_coverage_rate"]
    metrics["diagnosis_success_rate_pct"] = metrics["task_success_rate"]
    return metrics


def group_by_fault_type(rows: Sequence[dict]) -> dict[str, dict]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row.get("fault_type", "unknown"), []).append(row)
    return {fault_type: aggregate(items) for fault_type, items in sorted(grouped.items())}


METRIC_LABELS: dict[str, str] = {
    "cases": "用例数",
    "errors": "异常数",
    "task_success_rate": "任务成功率%",
    "rca_accuracy": "RCA 准确率(0-1)",
    "tool_selection_f1": "工具选择 F1",
    "tool_selection_precision": "工具选择 P",
    "tool_selection_recall": "工具选择 R",
    "tool_param_validity": "工具调用成功率%",
    "evidence_coverage_rate": "证据覆盖率%",
    "avg_tool_calls": "平均工具调用数",
    "avg_progress_events": "平均进度事件数",
    "redundant_call_rate": "冗余调用率%",
    "hallucination_rate": "幻觉率%",
    "tool_degraded_rate": "工具降级率%",
    "avg_duration_ms": "平均耗时ms",
    "avg_total_tokens": "平均 tokens",
}

FAULT_TYPE_ORDER = (
    "service_unreachable",
    "redis_memory",
    "kafka_lag",
    "mysql_connections",
    "container_oom",
    "slow_sql",
    "log_error_storm",
)


def sorted_fault_types(keys: Iterable[str]) -> list[str]:
    known = [fault for fault in FAULT_TYPE_ORDER if fault in set(keys)]
    extra = sorted(set(keys) - set(known))
    return known + extra


def failure_reasons(row: dict) -> list[str]:
    """给失败用例写清楚失败在哪一步（报告用）。"""
    reasons: list[str] = []
    if row.get("status") == "error":
        reasons.append("运行异常")
    if not row.get("required_tool_hit"):
        reasons.append(f"未命中必需工具（缺 {', '.join(row.get('missed_tools', [])) or '无'})")
    if row.get("evidence_hit_count", 0) < 1:
        reasons.append("答案无任何证据词")
    if row.get("hallucination"):
        reasons.append(f"命中错误归因: {', '.join(row.get('hallucination_phrases', []))}")
    return reasons

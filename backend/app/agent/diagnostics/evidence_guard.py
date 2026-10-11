"""证据约束（防幻觉）——事后闸门。

背景：模型偶尔会凭常识/快照给出**具体数字**（如"共 0 次""成功率 98%"），
但本轮**没有任何取证工具调用**，属于臆造。这里加一道轻量"事后闸门"：

  - 不改数字、不阻断，只在该情况下**加一句显式提示**；
  - 判定结果交给审计（谁在何时给出未取证的定量结论）。

判定规则（保守、低误报）：
  含具体数字（数字 + 量词/单位） 且 本轮没有任何"取证类工具"调用 → 标记。
只要出现过任一读取类工具，就认为回答有依据（避免对健康检查/指标/日志类结论误伤）。
"""
import logging
import re

log = logging.getLogger(__name__)

# 视为"取证"的只读工具：出现任一即认为回答有据可依。
EVIDENCE_TOOLS = frozenset({
    "query_business_dataset", "query_business_data",
    "query_prometheus", "query_prometheus_range",
    "list_services", "check_service",
    "read_logs", "search_logs", "read_service_logs", "search_service_logs",
    "run_kafka_command", "run_redis_command", "run_readonly_query",
    "list_archive_files", "read_archive_file", "fetch_archive_file", "run_python_analysis",
    "search_knowledge_base", "load_runbook",
})

# 具体数字：数字 + 常见量词/单位（次数、条数、百分比、字节、时长等）。
_QUANT = re.compile(
    r"\d[\d,\.]*\s*(?:次|条|个|项|份|台|家|起|%|％|字节|GB|MB|KB|TB|B|ms|s|秒|分钟|小时|天)"
)

CAVEAT = (
    "\n\n> ⚠️ 本回答包含具体数字，但本轮诊断中**未发现对应的取证工具调用**"
    "（业务数据集 / 指标 / 日志 / 健康检查 / 归档文件等）。请以工具取到的实际数据为准；"
    "如需确切数值，我可以重新查询核验。"
)


def has_quantitative_claims(answer: str) -> bool:
    return bool(_QUANT.search(answer or ""))


def evidence_tools_used(tool_calls) -> bool:
    for call in tool_calls or []:
        if str((call or {}).get("tool") or "") in EVIDENCE_TOOLS:
            return True
    return False


def apply_evidence_guard(answer: str, tool_calls, *, enabled: bool = True):
    """返回 (可能追加了提示的回答, 元信息 meta)。异常一律按原样返回。"""
    meta = {"has_claims": False, "evidence_tool_used": False, "flagged": False}
    text = answer or ""
    if not enabled:
        return text, meta
    try:
        meta["has_claims"] = has_quantitative_claims(text)
        meta["evidence_tool_used"] = evidence_tools_used(tool_calls)
        if meta["has_claims"] and not meta["evidence_tool_used"]:
            meta["flagged"] = True
            return text + CAVEAT, meta
    except Exception as exc:  # noqa: BLE001 - 校验失败绝不能影响诊断
        log.debug("[evidence-guard] 校验失败，按原样返回: %s", exc)
    return text, meta

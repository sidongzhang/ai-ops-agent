"""Conservative normalization of explicit tool failures, not a factual verifier.

A successful invocation does not prove that a model's conclusion is correct. We
preserve the existing evidence UI and do not enable unbounded model rechecks.
"""
import re


# A tool attempt alone does not establish evidence or verify a factual claim.
REPORT_EVIDENCE_TOOLS = frozenset({
    'query_business_dataset', 'query_business_data',
    'query_prometheus', 'query_prometheus_range',
    'list_services', 'check_service', 'check_container_state',
    'read_logs', 'search_logs', 'read_service_logs', 'search_service_logs',
    'run_kafka_command', 'run_redis_command', 'run_readonly_query',
    'list_archive_files', 'read_archive_file', 'fetch_archive_file', 'run_python_analysis',
    'search_knowledge_base', 'load_runbook', 'mcp_call',
})
_QUANT = re.compile(r'\d[\d,\.]*\s*(?:次|条|个|项|份|台|家|起|%|％|字节|GB|MB|KB|TB|B|ms|s|秒|分钟|小时|天)')
CAVEAT = (
    '\n\n> ⚠️ 本回答包含具体数字，但本轮诊断中**未发现对应的取证工具调用**'
    '（业务数据集 / 指标 / 日志 / 健康检查 / 归档文件等）。请以工具取到的实际数据为准。'
)
UNAVAILABLE_CAVEAT = (
    '\n\n> ⚠️ 本回答包含具体数字，但本轮诊断中**未获得成功的取证工具结果**'
    '（工具失败、被拒绝、超时、中断、结果为空或状态缺失）。这些数字尚未核实，请以实际查询结果为准。'
)


def has_quantitative_claims(answer: str) -> bool:
    return bool(_QUANT.search(answer or ''))


def evidence_tools_used(tool_calls) -> bool:
    """Whether an evidence tool was attempted, including failed attempts."""
    return any(call.get('tool') in REPORT_EVIDENCE_TOOLS for call in tool_calls or [] if isinstance(call, dict))


def evidence_available(tool_calls) -> bool:
    """Successful evidence exists; relevance and factual correctness are not checked."""
    return any(
        call.get('tool') in REPORT_EVIDENCE_TOOLS
        and call.get('status') == 'success'
        and bool(str(call.get('output') or '').strip())
        and outcome_for_text(str(call['output']))[0] == 'success'
        for call in tool_calls or [] if isinstance(call, dict)
    )


def apply_evidence_guard(answer: str, tool_calls, *, enabled: bool = True):
    """Annotate quantitative answers without evidence, without re-running the model.

    Unknown legacy result status stays unknown. Availability is not a claim-level
    verifier: a successful health check cannot establish an unrelated task count.
    """
    text = answer or ''
    meta = {'has_claims': False, 'evidence_tool_used': False, 'flagged': False,
            'evidence_available': False, 'verification': 'not_performed'}
    if not enabled:
        return text, meta
    meta['has_claims'] = has_quantitative_claims(text)
    meta['evidence_tool_used'] = evidence_tools_used(tool_calls)
    meta['evidence_available'] = evidence_available(tool_calls)
    from .claims import check_claims
    try:
        meta['claim_consistency'] = check_claims(text, tool_calls)
    except Exception:
        # Verification is an observer; failure must not retry the model or fail a completed run.
        meta['flagged'] = True
        meta['reason'] = 'validation_unavailable'
        meta['claim_consistency'] = {'checks': [], 'complete_answer_verified': False, 'error': 'validation_unavailable'}
        return text, meta
    meta['verification'] = 'bounded_checks_performed' if meta['claim_consistency']['checks'] else 'no_supported_claims'
    unresolved = [check for check in meta['claim_consistency']['checks'] if check['status'] != 'matches_tool_text']
    if meta['has_claims'] and not meta['evidence_available']:
        meta['flagged'] = True
        meta['reason'] = 'evidence_unavailable' if meta['evidence_tool_used'] else 'no_evidence_tool'
    elif unresolved:
        meta['flagged'] = True
        meta['reason'] = 'claim_evidence_unresolved'

    return text, meta


def outcome_for_result(text: str, *, outcome: str = 'success') -> tuple[str, str]:
    """Keep engine interruption/failure, then normalize explicit connector errors."""
    if outcome != 'success':
        return str(outcome), ''
    return outcome_for_text(text)


def outcome_for_text(text: str) -> tuple[str, str]:
    if text.startswith('【外部数据·') and '\n' in text:
        text = text.split('\n', 1)[1]
    if any(marker in text for marker in ('工具访问被拒绝：', '提示注入防护：')):
        return 'denied', 'connector_denied'
    if 'MCP 工具返回错误：' in text:
        return 'error', 'connector_error'
    if text.startswith(('工具参数无效：', '工具执行失败：', '未找到 MCP server')):
        return 'error', 'connector_error'
    if text.startswith('工具超时：'):
        return 'timeout', 'deadline_exceeded'
    body = text.split('\n', 1)[1] if text.startswith('【外部数据·') and '\n' in text else text
    if body.startswith(('Prometheus 查询失败:', '远程 Prometheus 查询失败:', '远程 Kafka 查询失败:',
                        '远程 Redis 查询失败:', 'Redis 命令执行失败:', '执行失败:',
                        '连接 MCP server', '调用 MCP 工具失败', 'arguments_json 不是合法 JSON',
                        'arguments_json 必须是 JSON 对象', '系统中无服务', '当前系统登记的 Kafka 服务没有',
                        '未找到 Kafka 容器', '未找到 Prometheus 地址', '未找到 Redis 服务',
                        '当前系统未启用', '当前系统未配置', 'docker inspect ')):
        return 'error', 'connector_unavailable'
    if body.startswith('命令超时'):
        return 'timeout', 'connector_timeout'
    return 'success', ''

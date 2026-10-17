"""Pydantic-specific history codec. Raw records stay untouched in platform storage."""
import json
from datetime import datetime, timezone

def load_messages(value):
    if value is None or not isinstance(value, str):
        return value  # compatibility for existing direct Python callers
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    return ModelMessagesTypeAdapter.validate_json(value)


def _usage_payload(value):
    from .contracts import RuntimeCompatibilityError

    try:
        raw = json.loads(value)
        if not isinstance(raw, dict):
            raise ValueError('usage must be an object')
        for name in ('requests', 'tool_calls'):
            if name not in raw:
                raise ValueError('admission baseline missing')
        for name in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens',
                     'requests', 'reported_requests', 'tool_calls', 'reported_tool_calls'):
            if name in raw and (type(raw[name]) is not int or raw[name] < 0):
                raise ValueError('invalid usage counter')
        if raw.get('reported_requests', raw['requests']) > raw['requests']:
            raise ValueError('reported count exceeds admitted count')
        if raw.get('reported_tool_calls', raw['tool_calls']) > raw['tool_calls']:
            raise ValueError('reported tool count exceeds admitted count')
        return raw
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeCompatibilityError('历史用量缺失或损坏，无法安全继承预算，不能按零用量恢复') from exc


def usage_context(value):
    """Keep platform uncertainty beside, rather than inside, native RunUsage."""
    if not isinstance(value, str):
        return {}
    raw = _usage_payload(value)
    gaps = []
    if raw.get('usage_complete') is not True or raw.get('cost_complete') is not True:
        gaps.append(str(raw.get('gap_reason') or 'prior_usage_incomplete'))
    offset = raw['requests'] - raw.get('reported_requests', raw['requests'])
    if offset:
        gaps.append('prior_model_request_usage_unconfirmed')
    if not all(name in raw for name in ('input_tokens', 'output_tokens')):
        gaps.append('prior_token_usage_missing')
    return {'gaps': gaps, 'reported_requests_offset': offset,
            'reported_tool_calls_offset': raw['tool_calls'] - raw.get('reported_tool_calls', raw['tool_calls'])}


def load_usage(value):
    if value is None or not isinstance(value, str):
        return value
    from pydantic_ai.usage import RunUsage

    usage = RunUsage()
    raw = _usage_payload(value)
    for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "requests", "tool_calls"):
        setattr(usage, name, raw.get(name, 0))
    # Preserve current resume accounting; money baseline remains in the platform ledger.
    return usage


def prepare_history(messages_json: str, *, force_refresh: bool, ttl_for_tool):
    from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, ToolReturnPart, repair_messages

    history = repair_messages(load_messages(messages_json))
    stale = []
    now = datetime.now(timezone.utc)
    for message in history:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            stamp = getattr(part, "timestamp", None)
            if not isinstance(stamp, datetime):
                continue
            stamp = stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
            age = max(0.0, (now - stamp).total_seconds() / 60)
            ttl = ttl_for_tool(part.tool_name)
            if age * 60 <= ttl:
                continue
            stale.append({"tool": part.tool_name, "age_minutes": round(age, 1), "ttl_seconds": ttl})
            if force_refresh:
                part.content = (
                    f"（该证据采集于约 {age:.0f} 分钟前，已超过 {ttl // 60} 分钟时效，"
                    "可能过期——需要时请重新调用工具取证）"
                )
    return ModelMessagesTypeAdapter.dump_json(history).decode("utf-8"), stale

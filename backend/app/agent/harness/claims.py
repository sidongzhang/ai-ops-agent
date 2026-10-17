"""Bounded text consistency checks, never a general factual verifier.

Explicit dataset.field totals and bounded health statements are supported.
No semantic inference, extra model requests, or automatic evidence refresh.
"""
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .freshness import ttl_seconds

_TOTAL = re.compile(r'(?<![\w.-])([A-Za-z_][\w-]{0,80})\.([A-Za-z_][\w]{0,80})\s*(?:合计|总计|=|为|是)\s*([0-9][0-9,.]*)(?![A-Za-z0-9_.])')



def _number(value):
    try:
        if not re.fullmatch(r'(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?', str(value)):
            return None
        number = Decimal(str(value).replace(',', ''))
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def check_claims(answer, tool_calls, *, now=None):
    from .evidence import outcome_for_text

    now = now or datetime.now(timezone.utc)
    claims = [dict(kind='dataset_total', subject=m[1], field=m[2], claimed=m[3])
              for m in _TOTAL.finditer(answer[:20000])]

    checks = []
    for claim in claims[:32]:
        candidates = []
        for call in (tool_calls or [])[-256:]:
            if not isinstance(call, dict) or call.get('status') != 'success' or not call.get('call_id'):
                continue
            args = call.get('input') or {}
            if not isinstance(args, dict):
                continue
            body = str(call.get('output') or '')
            if outcome_for_text(body)[0] != 'success':
                continue
            if body.startswith('【外部数据·'):
                body = body.split('\n', 1)[-1]
            observed = None
            if claim['kind'] == 'dataset_total' and call.get('tool') == 'query_business_dataset' and args.get('dataset') == claim['subject']:
                if not body.startswith('数据集 ' + claim['subject'] + '（'):
                    continue
                summary = re.search(r'^汇总（对以上 \d+ 行求和，[^\n]*）：([^\n]*)', body, re.M)
                if summary:
                    match = re.search(r'(?:^|，)' + re.escape(claim['field']) + r' 合计 ([0-9][0-9,.]*)', summary[1])
                    if match:
                        observed = match[1]
            if observed is None:
                continue
            fresh = False
            try:
                stamp = datetime.fromisoformat(call.get('observed_at') or '')
                if stamp.tzinfo is not None:
                    age = (now - stamp).total_seconds()
                    fresh = 0 <= age <= ttl_seconds(call['tool'])
            except (ValueError, TypeError):
                pass
            # A cache hit is not a new observation. The original call can still
            # supply evidence while its own timestamp remains fresh.
            fresh = fresh and '本次诊断已查过相同参数' not in body
            candidates.append((call['call_id'], observed, fresh,
                               json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)))
        fresh = [candidate for candidate in candidates if candidate[2]]
        status = 'unavailable'
        if candidates and not fresh:
            status = 'stale_or_undated'
        if fresh:
            scopes = {candidate[3] for candidate in fresh}
            values = {_number(candidate[1]) for candidate in fresh}
            if None in values or _number(claim['claimed']) is None:
                status = 'unavailable'
            elif len(scopes) > 1 or len(values) > 1:
                status = 'ambiguous'
            elif _number(claim['claimed']) in values:
                status = 'matches_tool_text'
            else:
                status = 'conflicts_tool_text'
        checks.append({**claim, 'status': status,
                       'tool_call_ids': [candidate[0] for candidate in fresh],
                       'observed_values': list(dict.fromkeys(candidate[1] for candidate in fresh))})
    from .health_claims import check_health_claims
    checks.extend(check_health_claims(answer, tool_calls or [], now=now))
    return {'scope': 'explicit_health_claims_and_dataset_totals',
            'checks': checks, 'complete_answer_verified': False,
            'truncated': len(claims) > 32 or len(answer) > 20000 or len(tool_calls or []) > 256}

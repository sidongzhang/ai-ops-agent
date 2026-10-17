"""Versioned Pi checkpoint projection; never convert histories across engines."""
import json
import math
import time
from copy import deepcopy

from .contracts import RuntimeCompatibilityError

PI_HISTORY_VERSION = 2


def load_checkpoint(raw, *, require_confirmed=True):
    try:
        document = json.loads(raw)
        if (not isinstance(document, dict) or document.get('format') != 'aiops-pi-v2'
                or (require_confirmed and document.get('stop_confirmed') is not True) or not isinstance(document.get('scope'), dict)):
            raise ValueError('not confirmed')
        if not isinstance(document.get('stop_confirmed'),bool) or not isinstance(document.get('model'),str) or not document['model'] or not isinstance(document.get('conversation_id'),str) or not isinstance(document.get('usage'),dict):
            raise ValueError('missing checkpoint identity')
        if set(document['scope'])!={'org_id','system_id'} or not all(isinstance(document['scope'][k],int) and document['scope'][k]>=0 for k in ('org_id','system_id')):
            raise ValueError('bad scope')
        messages = document['messages']
        if not isinstance(messages, list) or not messages:
            raise ValueError('no history')
        allowed = {'system', 'user', 'assistant', 'toolResult'}
        if not all(isinstance(m, dict) and m.get('role') in allowed for m in messages):
            raise ValueError('bad role')
        return document
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeCompatibilityError('Pi历史损坏、旧格式或执行停止尚未确认，不可继续') from exc


def project_checkpoint(raw, *, force_refresh=False, ttl_for_tool=lambda _:300):
    document = deepcopy(load_checkpoint(raw))
    messages = document['messages']
    # Pi's provider adapters remove aborted/error assistant turns. Preserve completed
    # tools from such a turn by projecting its finalized tool-call blocks as toolUse.
    for message in messages:
        if message.get('role') == 'assistant' and message.get('stopReason') in ('error', 'aborted'):
            calls = [c for c in message.get('content', []) if c.get('type') == 'toolCall']
            if calls:
                message['content'] = calls
                message['stopReason'] = 'toolUse'
                message.pop('errorMessage', None)
    known = {m['toolCallId'] for m in messages if m.get('role') == 'toolResult'}
    repaired = []
    for message in messages:
        repaired.append(message)
        if message.get('role') == 'assistant':
            for call in message.get('content', []):
                if call.get('type') == 'toolCall' and call.get('id') not in known:
                    repaired.append({'role':'toolResult','toolCallId':call['id'],'toolName':call['name'],
                        'content':[{'type':'text','text':'该调用被中断，没有可用结果；需要时重新取证'}],
                        'isError':True,'timestamp':message.get('timestamp', int(time.time()*1000))})
                    known.add(call['id'])
    stale = []
    for message in repaired:
        if message.get('role') != 'toolResult':
            continue
        stamp = message.get('timestamp')
        if not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
            continue
        age = max(0, time.time() - stamp / 1000)
        ttl = ttl_for_tool(message['toolName'])
        if age > ttl:
            stale.append({'tool':message['toolName'],'age_minutes':round(age/60,1),'ttl_seconds':ttl})
            if force_refresh:
                message['content'] = [{'type':'text','text':'该证据已过期，请重新调用工具取证'}]
    document['messages'] = repaired
    return json.dumps(document, ensure_ascii=False), stale

"""Ephemeral per-run Pi capabilities; Redis routes callbacks to their owner."""
import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

from .gateway import RunScope, ToolGateway
from .catalog import tool_catalog

PI_READ_TOOLS = frozenset(('list_services', 'check_service', 'check_container_state', 'read_logs',
                          'search_logs', 'query_prometheus', 'run_kafka_command', 'run_redis_command', 'search_knowledge_base'))


@dataclass
class ToolBinding:
    scope: RunScope
    deps: object
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    expires: float = field(default_factory=lambda: time.monotonic() + 240)
    claims: dict = field(default_factory=dict)
    lock: object = field(default_factory=threading.Lock)
    rpc_stop: object = None
    rpc_busy: object = None


_bindings = {}
_lock = threading.Lock()


def register_binding(scope, deps):
    binding = ToolBinding(scope, deps)
    with _lock:
        if scope.run_id in _bindings:
            raise ValueError('运行身份重复')
        _bindings[scope.run_id] = binding
    from .bridge_rpc import serve_binding
    try:
        binding.rpc_stop = serve_binding(scope.run_id, binding)
    except Exception:
        remove_binding(scope.run_id)
        raise
    return binding


def remove_binding(run_id):
    with _lock:
        binding = _bindings.pop(run_id, None)
    if binding and binding.rpc_stop:
        binding.rpc_stop.set()
    if binding:
        cancel = getattr(getattr(binding.deps, "cancel_token", None), "cancel", None)
        if callable(cancel):
            cancel()  # Fence a callback arriving as its run is being retired.


def authorized_binding(run_id, bearer):
    with _lock:
        binding = _bindings.get(run_id)
    if binding is None or time.monotonic() > binding.expires or not hmac.compare_digest(bearer or '', binding.token):
        return None
    return binding


async def invoke_bound(binding, call_id, tool, arguments):
    if tool not in PI_READ_TOOLS:
        return 403, {'error': 'tool_not_allowed'}
    digest = hashlib.sha256(json.dumps([tool, arguments], sort_keys=True).encode()).hexdigest()
    with binding.lock:
        old = binding.claims.get(call_id)
        if old:
            rec=getattr(binding.deps,'performance',None)
            if rec:
                span=rec.begin('replay',name=tool,call_id=call_id)
                rec.end(span,'replayed' if old.get('result') is not None else 'in_progress')
            if old['digest'] != digest:
                return 409, {'error': 'call_id_conflict'}
            return (200, old['result']) if old.get('result') is not None else (409, {'error': 'call_in_progress'})
        if len(binding.claims) >= 24:
            return 429, {"error": "tool_call_limit"}
        binding.claims[call_id] = {'digest': digest, 'result': None}
    ctx = SimpleNamespace(deps=binding.deps, tool_call_id=call_id)
    result = await ToolGateway().invoke(tool, arguments, ctx, expected_scope=binding.scope)
    payload = {'text': result.text, 'status': result.status, 'error_code': result.error_code}
    with binding.lock:
        binding.claims[call_id]['result'] = payload
    return 200, payload


def pi_tool_directory():
    return [spec.describe() for spec in tool_catalog() if spec.name in PI_READ_TOOLS and spec.risk == 'read']

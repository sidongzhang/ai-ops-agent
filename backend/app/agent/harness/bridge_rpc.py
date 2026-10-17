"""Bounded Redis request/reply transport to the process owning a Pi binding.

No dependencies or user credentials are serialized. The owner validates the
ephemeral capability and invokes the same ToolGateway, including its dedup,
scope, cancellation and audit policy. Requests are never automatically retried.
"""
import asyncio
import json
import logging
import threading
import time
from uuid import uuid4

log = logging.getLogger(__name__)
PREFIX = "aiops:pi:tool-rpc:"
MAX_FRAME = 128 * 1024


def client(*, read_timeout=2):
    import redis
    from app.core.config import settings
    return redis.Redis.from_url(settings.celery_broker_url, socket_connect_timeout=2,
        socket_timeout=read_timeout, decode_responses=True)


def binding_status(binding, run_id):
    tracker = getattr(binding.deps, "execution_tracker", None)
    busy = getattr(binding, "rpc_busy", None)
    waiting = bool(busy and busy.locked())
    return {"run_id": run_id, "active": max(tracker.active_count() if tracker else 0, int(waiting)),
            "stop_confirmed": (tracker.stop_confirmed() if tracker else True) and not waiting}


def serve_binding(run_id, binding):
    """Subscribe before sending the capability to Pi, so first call cannot race."""
    conn = client()
    channel = PREFIX + "run:" + run_id
    pubsub = conn.pubsub()
    try:
        pubsub.subscribe(channel)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            message = pubsub.get_message(timeout=1)
            if message and message.get("type") == "subscribe":
                break
        else:
            raise RuntimeError("Pi tool callback subscription unavailable")
    except Exception:
        pubsub.close()
        conn.close()
        raise
    stop = threading.Event()
    busy = threading.Lock()
    binding.rpc_busy = busy

    def reply(request_id, status, payload):
        key = PREFIX + "reply:" + request_id
        frame = json.dumps([status, payload], ensure_ascii=False)
        # Expiration also removes late replies after an HTTP disconnect/timeout.
        with conn.pipeline() as pipeline:
            pipeline.rpush(key, frame)
            pipeline.expire(key, 70)
            pipeline.execute()

    def invoke(frame):
        from .bridge import invoke_bound
        from .gateway import ToolCancelled, ToolAuditError
        try:
            status, payload = asyncio.run(invoke_bound(binding, frame["body"]["call_id"],
                frame["body"]["tool"], frame["body"]["arguments"]))
        except ToolCancelled:
            status, payload = 409, {"detail": "run_cancelled"}
        except ToolAuditError:
            status, payload = 503, {"detail": "tool_audit_unavailable"}
        except Exception:
            status, payload = 503, {"detail": "tool_execution_unavailable"}
        try:
            reply(frame["request_id"], status, payload)
        except Exception:
            log.warning("[pi-rpc] tool reply unavailable")
        finally:
            busy.release()

    def listen():
        from .bridge import authorized_binding, PI_READ_TOOLS
        while not stop.is_set() and time.monotonic() <= binding.expires:
            try:
                message = pubsub.get_message(timeout=1)
                if not message or message.get("type") != "message":
                    continue
                raw = message["data"]
                if len(raw.encode()) > MAX_FRAME:
                    continue
                frame = json.loads(raw)
                request_id = frame.get("request_id", "")
                if len(request_id) != 32 or any(c not in "0123456789abcdef" for c in request_id):
                    continue
                if authorized_binding(run_id, frame.get("token")) is not binding:
                    reply(request_id, 401, {"detail": "invalid_run_capability"})
                elif frame.get("operation") == "status":
                    reply(request_id, 200, binding_status(binding, run_id))
                elif frame.get("operation") != "call":
                    reply(request_id, 400, {"detail": "invalid_operation"})
                elif not isinstance(frame.get("body"), dict) or frame["body"].get("tool") not in PI_READ_TOOLS:
                    reply(request_id, 403, {"detail": "tool_not_allowed"})
                elif busy.acquire(blocking=False):
                    threading.Thread(target=invoke, args=(frame,), daemon=True).start()
                else:
                    reply(request_id, 409, {"detail": "tool_call_in_progress"})
            except Exception:
                if not stop.is_set():
                    log.warning("[pi-rpc] callback listener unavailable")
                    stop.wait(.5)
        pubsub.close()
        conn.close()

    thread = threading.Thread(target=listen, daemon=True, name="pi-tool-callback")
    thread.start()
    return stop


def forward(run_id, bearer, operation, body=None):
    if not bearer or len(bearer) < 32 or len(run_id) > 200:
        return 401, {"detail": "invalid_run_capability"}
    request_id = uuid4().hex
    timeout = 60 if operation == "call" else 1
    frame = json.dumps({"request_id": request_id, "token": bearer,
        "operation": operation, "body": body}, ensure_ascii=False)
    if len(frame.encode()) > MAX_FRAME:
        return 413, {"detail": "arguments_too_large"}
    conn = client(read_timeout=timeout + 2)
    key = PREFIX + "reply:" + request_id
    try:
        # Pub/sub carries no replay backlog, so an absent owner fails closed.
        if conn.publish(PREFIX + "run:" + run_id, frame) == 0:
            return 401, {"detail": "invalid_run_capability"}
        reply = conn.blpop(key, timeout=timeout)
        if reply is None:
            return 503, {"detail": "tool_owner_timeout"}
        status, payload = json.loads(reply[1])
        return int(status), payload
    except Exception:
        return 503, {"detail": "tool_owner_unavailable"}
    finally:
        conn.close()

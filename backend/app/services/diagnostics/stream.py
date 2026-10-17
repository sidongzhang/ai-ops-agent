"""SSE snapshots backed by the report, so reconnects never depend on one worker.

Every snapshot includes accumulated evidence. The cursor identifies its contents;
reconnecting clients receive the latest complete snapshot, not an incomplete delta.
"""
import asyncio
import hashlib
import json
import time

from .session import session as db_session
from .reports import get_diagnosis_report


def read_snapshot(report_id: int, system_id: int, org_id: int) -> dict:
    with db_session() as db:
        report = get_diagnosis_report(db, system_id, org_id, report_id)
        return report.model_dump(mode="json")


def encode_snapshot(snapshot: dict) -> tuple[str, str]:
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    cursor = hashlib.sha256(payload.encode()).hexdigest()
    return cursor, f"id: {cursor}\nevent: report\ndata: {payload}\n\n"


async def report_events(request, report_id: int, system_id: int, org_id: int, cursor: str = ""):
    from redis.asyncio import Redis
    from app.core.config import settings
    from .answer_stream import key, channel
    client = subscription = None
    last_text = None
    snapshot = None
    next_report = 0.
    heartbeat = time.monotonic()
    first = True
    try:
        try:
            client = Redis.from_url(settings.celery_broker_url, socket_timeout=.3, socket_connect_timeout=.3)
            subscription = client.pubsub()
            await subscription.subscribe(channel(report_id))
        except Exception:
            if subscription: await subscription.aclose()
            subscription = None
        while not await request.is_disconnected():
            now = time.monotonic()
            if now >= next_report:
                try:
                    snapshot = await asyncio.to_thread(read_snapshot, report_id, system_id, org_id)
                except LookupError:
                    yield 'event: unavailable\ndata: {"reason":"report_unavailable"}\n\n'
                    return
                revision, frame = encode_snapshot(snapshot)
                if first or revision != cursor:
                    yield frame
                    cursor, first = revision, False
                    heartbeat = now
                if snapshot["status"] in {"success", "failed", "cancelled"}:
                    yield 'event: done\ndata: {}\n\n'
                    return
                next_report = now + 1
            # Only read Redis after an authorized report lookup. Full numbered text
            # checkpoints repair dropped notifications and reconnects without replay.
            if client:
                try:
                    raw = await client.get(key(report_id))
                    if raw:
                        text = json.loads(raw)
                        revision = (text.get('stream_id'), text.get('sequence'))
                        if revision != last_text:
                            yield 'event: answer\ndata: ' + json.dumps({'id':report_id, 'answer_stream':text}, ensure_ascii=False) + '\n\n'
                            last_text = revision
                            heartbeat = now
                except Exception:
                    pass
            if now - heartbeat >= 15:
                yield ': heartbeat\n\n'
                heartbeat = now
            if subscription:
                try:
                    await subscription.get_message(ignore_subscribe_messages=True, timeout=.1)
                except Exception:
                    await subscription.aclose()
                    subscription = None
                    await asyncio.sleep(.1)
            else:
                await asyncio.sleep(.1)
    finally:
        if subscription: await subscription.aclose()
        if client: await client.aclose()

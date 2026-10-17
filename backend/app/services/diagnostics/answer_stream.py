"""Best-effort display stream. Never evidence, model history, or execution authority.

One owner writes each report. Redis keeps a replaceable snapshot and lossy wakeups;
periodic DB checkpoints bound draft loss without writing once per token.
"""
import copy
import json
import logging
import threading
import time
from uuid import uuid4

from redis import Redis
from sqlalchemy import update, or_
from app.core.config import settings
from app.models.diagnostics import DiagnosisReport

log = logging.getLogger(__name__)
TTL = 3600
MAX_CHARS = 100_000
MAX_MESSAGES = 128


def redis_client():
    return Redis.from_url(settings.celery_broker_url, socket_timeout=.3, socket_connect_timeout=.3)


def key(report_id):
    return f'aiops:answer:{report_id}'


def channel(report_id):
    return key(report_id) + ':notify'


def read_cached(report_id):
    try:
        raw = redis_client().get(key(report_id))
        return json.loads(raw) if raw else None
    except Exception:
        return None


class AnswerRecorder:
    def __init__(self, report_id, org_id, system_id):
        self.report_id, self.org_id, self.system_id = report_id, org_id, system_id
        self.state = {'version': 1, 'stream_id': uuid4().hex, 'sequence': 0, 'messages': []}
        self.lock = threading.RLock()
        self.changed = threading.Event()
        self.stop = threading.Event()
        self.last_db = 0.
        self.closed = False
        self.thread = threading.Thread(target=self._pump, daemon=True, name=f'answer-{report_id}')
        self.thread.start()

    def record(self, event):
        kind = event.get('kind', '')
        if not kind.startswith('answer_'):
            return
        with self.lock:
            messages = self.state['messages']
            if kind == 'answer_tools':
                if messages and messages[-1]['state'] != 'failed': messages[-1]['state'] = 'process'
            elif kind == 'answer_attempt':
                for item in messages:
                    if item['state'] != 'failed': item['state'] = 'failed'
            else:
                message_id = str(event.get('message_id', ''))[:100]
                if not message_id: return
                item = next((m for m in messages if m['id'] == message_id), None)
                if item is None:
                    if len(messages) >= MAX_MESSAGES:
                        self.state["truncated"] = True
                        self.changed.set()
                        return
                    item = {'id': message_id, 'text': '', 'state': 'generating'}
                    messages.append(item)
                if kind == 'answer_delta':
                    remaining = MAX_CHARS - sum(len(m['text']) for m in messages)
                    incoming = str(event.get('text', ''))
                    chunk = incoming[:max(remaining, 0)]
                    if len(incoming) > len(chunk): self.state['truncated'] = True
                    item['text'] += chunk
                    if not chunk: return
                elif kind == 'answer_end' and item['state'] != 'process': item['state'] = 'generated'
                elif kind == 'answer_process': item['state'] = 'process'
            self.state['sequence'] += 1
            self.changed.set()

    def _snapshot(self):
        with self.lock: return copy.deepcopy(self.state)

    def _save(self, state):
        from .session import session as db_session
        try:
            with db_session() as db:
                db.execute(update(DiagnosisReport).where(
                    DiagnosisReport.id == self.report_id, DiagnosisReport.org_id == self.org_id,
                    DiagnosisReport.system_id == self.system_id,
                    or_(DiagnosisReport.answer_draft.is_(None),
                        DiagnosisReport.answer_draft["sequence"].as_integer().is_(None),
                        DiagnosisReport.answer_draft["sequence"].as_integer() <= state["sequence"]),
                ).values(answer_draft=state))
                db.commit()
            self.last_db = time.monotonic()
        except Exception:
            log.warning('Answer draft checkpoint unavailable report=%s', self.report_id)

    def _publish(self, state):
        try:
            client = redis_client()
            with client.pipeline() as pipe:
                pipe.set(key(self.report_id), json.dumps(state, ensure_ascii=False), ex=TTL)
                pipe.publish(channel(self.report_id), str(state['sequence']))
                pipe.execute()
        except Exception:
            pass  # DB checkpoints and final report remain available.

    def _pump(self):
        while not self.stop.wait(.1):
            if not self.changed.is_set(): continue
            self.changed.clear()
            state = self._snapshot()
            self._publish(state)
            if time.monotonic() - self.last_db >= 1: self._save(state)

    def close(self):
        if self.closed: return
        self.closed = True
        self.stop.set()
        self.thread.join(timeout=2)
        state = self._snapshot()
        self._save(state)
        self._publish(state)


def discard_cached(report_ids):
    """Called after authorized history deletion; final drafts share report lifetime."""
    if not report_ids: return
    try:
        client = redis_client()
        client.delete(*(key(rid) for rid in report_ids))
    except Exception:
        pass  # TTL bounds orphan retention; deleted reports cannot authorize reads.

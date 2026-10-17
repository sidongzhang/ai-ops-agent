"""Track actual executor futures, independent of cancelled asyncio wrappers."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from uuid import uuid4
from contextvars import ContextVar

_CURRENT_TRACKER = ContextVar('diagnosis_execution_tracker', default=None)


def mark_current_execution_unconfirmed():
    tracker=_CURRENT_TRACKER.get()
    if tracker is not None:
        tracker.mark_unconfirmed()

from ..runtime.contracts import RuntimeExecutionError
from .evidence import outcome_for_text


class ExecutionUnsettled(RuntimeExecutionError):
    """Execution did not confirm termination; never retry or resume automatically."""

_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix='diagnosis-tool')


class ExecutionTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self.futures = {}
        self.results = {}
        self.unconfirmed = False

    def submit(self, call_id, function):
        with self._lock:
            # Even a provider that reuses IDs must not hide an earlier executor.
            key = call_id if call_id not in self.futures else f'{call_id}:{uuid4().hex}'
            def execute():
                token=_CURRENT_TRACKER.set(self)
                try:
                    return function()
                finally:
                    _CURRENT_TRACKER.reset(token)
            future = _EXECUTOR.submit(execute)
            self.futures[key] = future
        def completed(done):
            try:
                value = done.result()
                text = str(value)
                result = {'text': text, 'status': 'unknown' if outcome_for_text(text)[0]=='timeout' else 'success'}
            except BaseException:
                result = {'text': '底层工具未完成或执行失败', 'status': 'error'}
            with self._lock:
                self.results[key] = result
                self._changed.notify_all()
        future.add_done_callback(completed)
        return future

    def active_count(self):
        with self._lock:
            return sum(not f.done() for f in self.futures.values())

    def stop_confirmed(self):
        with self._lock:
            return self._confirmed()

    def _confirmed(self):
        return not self.unconfirmed and len(self.results)==len(self.futures) and all(f.done() for f in self.futures.values()) and all(r['status']!='unknown' for r in self.results.values())

    def mark_unconfirmed(self):
        with self._changed:
            self.unconfirmed = True
            self._changed.notify_all()

    def wait_for_stop(self, timeout):
        deadline=time.monotonic()+max(0,timeout)
        with self._changed:
            while len(self.results)!=len(self.futures) or any(not f.done() for f in self.futures.values()):
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    return False
                self._changed.wait(remaining)
            return self._confirmed()


def observe_remote_command(command, tracker):
    if command is None:
        return None
    def execute(*args, **kwargs):
        result=command(*args, **kwargs)
        if isinstance(result, dict) and result.get('execution_confirmed') is False:
            tracker.mark_unconfirmed()
        return result
    return execute

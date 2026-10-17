"""Best-effort, bounded observer. Never stores arguments, results or exception text.

Offsets use one worker monotonic clock. Nested spans overlap; their sum is not
wall time. Running spans retain null duration on crash/cancel until confirmed.
"""
import hashlib
import hmac
import json
import secrets
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import uuid4

CURRENT = ContextVar('diagnosis_performance', default=None)
CALL = ContextVar('diagnosis_performance_call', default=None)

def current():
    call=CALL.get()
    return CURRENT.get() or (call[0] if call else None)

def observer(deps): return getattr(deps, 'performance', None)

class Recorder:
    def __init__(self, persist=None, *, queue_ms=None):
        self.started=time.monotonic()
        self.anchor=datetime.now(timezone.utc).isoformat()
        self.events=[]; self.lock=threading.RLock(); self.persist=persist
        self.secret=secrets.token_bytes(32)
        self.queue_ms=queue_ms; self.first_text_ms=None; self.ready_ms=None; self.terminal_status=None
        self.incomplete=[]; self.budgets={}; self.closed=False; self.external_ids={}
        self.stop=threading.Event(); self.write_lock=threading.Lock()
        self.thread=None
        if persist:
            self.thread=threading.Thread(target=self._checkpoint,daemon=True)
            self.thread.start()

    def offset(self): return round((time.monotonic()-self.started)*1000, 2)

    def gap(self, reason):
        with self.lock:
            if reason not in self.incomplete: self.incomplete.append(reason)

    def begin(self, stage, *, name='', agent='main', parent=None, call_id=None, arguments=None):
        try:
            with self.lock:
                if self.closed: return None
                if len(self.events)>=2048:
                    self.gap('event_limit'); return None
                item=dict(id=uuid4().hex,stage=stage,name=name,agent=agent,parent_call_id=parent,
                    call_id=call_id,start_ms=self.offset(),duration_ms=None,status='running')
                if arguments is not None:
                    raw=json.dumps(arguments,sort_keys=True,ensure_ascii=False,default=lambda _: '<unsupported>').encode()
                    # Per-run HMAC prevents dictionary recovery of low-entropy secrets.
                    item['arguments_digest']=hmac.new(self.secret,raw,hashlib.sha256).hexdigest()[:24]
                self.events.append(item)
                return item['id']
        except Exception:
            self.gap('event_capture_failed'); return None

    def update(self, span, **fields):
        try:
            with self.lock:
                if self.closed: return
                for event in reversed(self.events):
                    if event['id']==span:
                        event.update(fields); return
        except Exception: self.gap('event_update_failed')

    def end(self, span, status='success'):
        try:
            with self.lock:
                if self.closed: return
                for event in reversed(self.events):
                    if event['id']==span:
                        event.update(duration_ms=round(self.offset()-event['start_ms'],2),status=status);return
        except Exception: self.gap('event_finish_failed')

    @contextmanager
    def span(self, stage, **kw):
        ident=self.begin(stage,**kw)
        try: yield ident
        except BaseException as exc:
            self.end(ident, 'cancelled' if type(exc).__name__ in ('RunCancelled','CancelledError','ToolCancelled') else 'failed')
            raise
        else: self.end(ident)

    def text(self):
        try:
            with self.lock:
                if self.first_text_ms is None: self.first_text_ms=self.offset()
        except Exception: self.gap('first_text_missing')

    def budget(self, deps):
        try:
            with self.lock:
                for kind in ('request','tool'):
                    budget=getattr(deps,kind+'_budget',None)
                    if budget is not None:
                        previous=self.budgets.get(kind,{}).get('baseline',budget.used)
                        self.budgets[kind]={**self.budgets.get(kind,{}),**dict(used=budget.used,baseline=previous,attempt_used=max(0,budget.used-previous),limit=budget.limit,limit_scope='cumulative')}
        except Exception: self.gap('budget_snapshot_failed')

    def pi_snapshot(self, data, limits):
        try:
            if not data:
                self.gap('pi_model_metrics_missing'); return
            with self.lock:
                for item in data.get('models',[]):
                    ident=self.external_ids.get(item['id'])
                    if ident is None:
                        ident=self.begin('model',name='pi',agent='main')
                        self.external_ids[item['id']]=ident
                    self.update(ident,start_ms=item['start_ms'],duration_ms=item['duration_ms'],
                        status=item['status'],clock='pi',note='Pi进程相对时间，不与worker阶段直接相加')
                for kind in ('request','tool'):
                    baseline=data[kind+'_baseline']
                    self.budgets[kind]=dict(baseline=baseline,used=baseline+data[kind+'_used'],
                        attempt_used=data[kind+'_used'],limit_scope='attempt',limit=limits['requests' if kind=='request' else 'tools'])
        except Exception: self.gap('pi_metrics_invalid')

    def snapshot(self):
        with self.lock:
            stages={}
            for stage in ('context','model','tool','tool_execution','report'):
                items=[e for e in self.events if e['stage']==stage]
                done=[e for e in items if e['duration_ms'] is not None]
                # Union of intervals is a wall-clock occupancy, not a sum of overlaps.
                intervals=sorted((e['start_ms'],e['start_ms']+e['duration_ms']) for e in done)
                union=0; end=0
                for a,b in intervals:
                    union+=max(0,b-max(a,end)); end=max(end,b)
                stages[stage]=dict(count=len(items),completed=len(done),occupied_ms=round(union,2) if done else None)
            tools=[e for e in self.events if e['stage']=='tool']
            summary=dict(version=1,state='complete' if self.closed and not self.incomplete else 'incomplete' if self.incomplete else 'collecting',
                started_at=self.anchor,queue_ms=self.queue_ms,execution_ms=self.ready_ms,
                first_text_ms=self.first_text_ms,report_ready_ms=self.ready_ms if self.terminal_status=='success' else None,
                final_report_available=self.terminal_status=='success',terminal_ready_ms=self.ready_ms,
                total_ms=round(self.queue_ms+self.ready_ms,2) if self.queue_ms is not None and self.ready_ms is not None else None,
                stages=stages,model_requests=sum(e['stage']=='model' and e.get('admitted',True) for e in self.events),tool_requests=len(tools),tool_executions=sum(e['stage']=='tool_execution' for e in self.events),
                cache_hits=sum(e.get('cache')=='hit' for e in tools),
                tool_outcomes={s:sum(e['status']==s for e in tools) for s in ('success','error','timeout','denied','cancelled','failed','unknown','running')},
                budgets=dict(self.budgets),gaps=list(self.incomplete),
                by_agent={agent:dict(model_requests=sum(e['stage']=='model' and e.get('admitted',True) for e in self.events if e['agent']==agent),
                    tool_requests=sum(e['stage']=='tool' for e in self.events if e['agent']==agent),
                    tool_executions=sum(e['stage']=='tool_execution' for e in self.events if e['agent']==agent),
                    cache_hits=sum(e.get('cache')=='hit' for e in tools if e['agent']==agent),
                    tool_budget_admissions=sum(e.get('budget_admitted') is True for e in tools if e['agent']==agent)) for agent in {e['agent'] for e in self.events}},
                replays=sum(e['stage']=='replay' for e in self.events),
                repeated_requests=len(tools)-len({(e['name'],e.get('arguments_digest')) for e in tools}))
            summary['counts_complete']=not any(reason in self.incomplete for reason in ('event_limit','event_capture_failed','event_update_failed'))
            if any(reason in self.incomplete for reason in ('pi_model_metrics_missing','pi_metrics_invalid')):
                summary['model_requests']=None
                if not stages['model']['count']:
                    stages['model'].update(count=None,completed=None,occupied_ms=None)
            return summary,[dict(e) for e in self.events]

    def flush(self):
        if not self.persist: return
        try:
            with self.write_lock: self.persist(*self.snapshot())
        except Exception: self.gap('checkpoint_failed')

    def _checkpoint(self):
        while not self.stop.wait(1): self.flush()

    def finish(self, status=None):
        try:
            with self.lock:
                if self.closed: return
                self.terminal_status=status
                self.ready_ms=self.offset()
                for e in self.events:
                    if e['duration_ms'] is None:
                        e['status']='unknown'; self.gap('unfinished_spans')
                self.closed=True
            self.stop.set()
            if self.persist:
                writer=threading.Thread(target=self.flush,daemon=True)
                writer.start()
                # A telemetry backend outage must not hold the answer hostage.
                writer.join(.2)
        except Exception: self.gap('final_checkpoint_failed')

def active_call(deps):
    return CALL.get() or (observer(deps),None,None)

@contextmanager
def implementation(deps, name):
    rec, request, call_id=active_call(deps)
    if rec is None:
        yield;return
    with rec.span('tool_execution',name=name,agent=getattr(deps,'performance_agent','main'),
                  parent=getattr(deps,'performance_parent',None),call_id=call_id):
        rec.update(request,cache='miss')
        yield

def cache_hit(deps):
    rec, request, _ = active_call(deps)
    if rec: rec.update(request,cache='hit')


def measured(stage,name):
    """Time an existing synchronous preparation step, without capturing its data."""
    import functools
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args,**kwargs):
            rec=current()
            if rec is None: return function(*args,**kwargs)
            active=CALL.get()
            parent=active[2] if active else None
            agent='main'
            if active:
                with rec.lock:
                    source=next((e for e in rec.events if e['id']==active[1]),None)
                    if source: agent=source['agent']
            with rec.span(stage,name=name,agent=agent,parent=parent):
                return function(*args,**kwargs)
        return wrapped
    return decorate

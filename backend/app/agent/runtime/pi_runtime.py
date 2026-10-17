"""PiNode client: bounded read-only trial, never fallback to another engine."""
import json
import logging
import threading
import time
from uuid import uuid4

import httpx

from app.core.config import settings
from .contracts import RuntimeCapabilities, RuntimeCompatibilityError, RuntimeExecutionError, RuntimeBudgetExceeded, RuntimeEvent, RuntimeResult
from ..harness.bridge import register_binding, remove_binding, pi_tool_directory
from ..harness.gateway import RunScope
from ..harness.execution import ExecutionTracker
from .pi_history import PI_HISTORY_VERSION, load_checkpoint, project_checkpoint

log = logging.getLogger(__name__)
PI_VERSION = 'pi-agent-core@1.2.0+pi-ai@1.2.0'


class PiCancellation:
    def __init__(self):
        self._event = threading.Event()

    @property
    def cancelled(self):
        return self._event.is_set()

    def cancel(self):
        self._event.set()


def validate_trial_options(options):
    if options.get('images') or options.get('model_name') or options.get('model_mode', 'auto') not in ('auto', 'default'):
        raise RuntimeCompatibilityError('Pi只读试点只支持默认模型配置与文本输入')
    if options.get('message_history') or options.get('prior_usage'):
        raise RuntimeCompatibilityError('Pi历史继续尚未验收，不能恢复或接入其他引擎历史')


class PiRuntime:
    def capabilities(self):
        return RuntimeCapabilities('pi', PI_VERSION, images=False, subagents=False, cancellation=True, resume=True, history_schema_version=PI_HISTORY_VERSION)

    def create_cancellation(self):
        return PiCancellation()

    def validate_checkpoint(self, checkpoint):
        if checkpoint.engine != 'pi' or checkpoint.engine_version != PI_VERSION or checkpoint.history_schema_version != PI_HISTORY_VERSION:
            raise RuntimeCompatibilityError('Pi历史引擎或格式版本不兼容')
        load_checkpoint(checkpoint.messages_json, require_confirmed=False)

    def validate_resume_checkpoint(self, checkpoint):
        self.validate_checkpoint(checkpoint)
        load_checkpoint(checkpoint.messages_json)

    def prepare_resume(self, checkpoint, *, force_refresh, ttl_for_tool):
        self.validate_checkpoint(checkpoint)
        return project_checkpoint(checkpoint.messages_json, force_refresh=force_refresh, ttl_for_tool=ttl_for_tool)

    def preflight(self, options=None):
        if not settings.pi_runtime_enabled or len(settings.pi_service_secret) < 32:
            raise RuntimeCompatibilityError('Pi试点未启用或服务认证未配置')
        validate_trial_options(options or {})
        from urllib.parse import urlparse
        url = urlparse(settings.pi_runtime_url)
        if url.scheme != 'http' or url.hostname not in ('localhost', '127.0.0.1', '::1') or url.username or url.password or url.path not in ('', '/'):
            raise RuntimeCompatibilityError('Pi试点仅允许固定本机服务地址')
        try:
            with httpx.Client(timeout=5, follow_redirects=False) as client:
                response = client.get(settings.pi_runtime_url.rstrip('/') + '/healthz', headers=self.headers())
                response.raise_for_status()
                info = response.json()
                if info.get('status') != 'ok' or info.get('engine_version') != PI_VERSION or info.get('protocol_version') != 2:
                    raise ValueError('wrong runtime version')
        except Exception as exc:
            raise RuntimeCompatibilityError('Pi服务未就绪或版本不匹配') from exc

    def headers(self):
        return {'Authorization': f'Bearer {settings.pi_service_secret}'}

    def execute(self, request):
        self.preflight({k:v for k,v in request.options.items() if k not in ('message_history','prior_usage')})
        checkpoint = None
        if request.checkpoint is not None:
            self.validate_checkpoint(request.checkpoint)
            projected, _ = project_checkpoint(request.checkpoint.messages_json)
            checkpoint = load_checkpoint(projected)
            if checkpoint['scope'] != {'org_id':request.org_id,'system_id':request.system_id}:
                raise RuntimeCompatibilityError('Pi历史不属于当前组织与系统')
        elif request.options.get('message_history') or request.options.get('prior_usage'):
            raise RuntimeCompatibilityError('Pi只能从带版本与身份的checkpoint继续')
        from ..diagnostics.tools import AgentDeps
        from ..harness.context import build_system_prompt
        opts = request.options
        run_id = request.run_id or uuid4().hex
        token = opts.get('cancel_token') or self.create_cancellation()
        if token.cancelled:
            return RuntimeResult('', 'pi', 0, cancelled=True, engine='pi', engine_version=PI_VERSION)
        fields = {k:v for k,v in opts.items() if k in AgentDeps.__dataclass_fields__ and k not in ('descriptor','scope','gateway_records','control_flow_errors')}
        fields.pop('question', None)
        fields['cancel_token'] = token
        fields['execution_tracker'] = ExecutionTracker()
        deps = AgentDeps(request.descriptor, question=request.question, scope=RunScope(request.org_id, request.system_id, run_id), **fields)
        chat_only=opts.get('interaction_kind')=='chat'
        if chat_only: deps.allowed_tools=frozenset()
        binding = register_binding(deps.scope, deps)
        from ..harness.chat_profile import CHAT_RULES,chat_prompt
        body = {'run_id':run_id, 'scope':{'org_id':request.org_id,'system_id':request.system_id},
                'question':opts.get('prompt_override') or (chat_prompt(request.question,opts.get('chat_context')) if chat_only else request.question),'system_prompt':CHAT_RULES if chat_only else build_system_prompt(deps, allowed_tools={t["name"] for t in pi_tool_directory()}),
                'model_profile':'default','gateway_token':binding.token,'tools':[] if chat_only else pi_tool_directory(),
                'limits':{'requests':min(settings.diagnosis_request_limit,12),'tools':min(settings.diagnosis_tool_calls_limit,24),'tokens':min(settings.diagnosis_token_limit,100000)}}
        if checkpoint is not None: body['checkpoint'] = checkpoint
        url = settings.pi_runtime_url.rstrip('/')
        answer_sequence = -1
        answer_lengths = {}
        sequence = 0; settled = False; cancel_sent = False; last_snapshot = None
        def result_of(snapshot):
            history = {'format':'aiops-pi-v2','messages':snapshot['messages'],'scope':body['scope'],
                'usage':snapshot['usage'],'conversation_id':snapshot.get('conversation_id',run_id),
                'model':snapshot['model'],'stop_confirmed':snapshot.get('stop_confirmed',False)}
            return RuntimeResult(snapshot.get('answer',''),snapshot['model'],snapshot['duration_ms'],
                total_tokens=snapshot['usage']['total_tokens'],tool_calls=deps.gateway_records,
                cancelled=snapshot['status']=='cancelled',messages_json=json.dumps(history,ensure_ascii=False),
                conversation_id=history['conversation_id'],run_id=run_id,usage_json=json.dumps(snapshot['usage']),engine='pi',engine_version=PI_VERSION,
                history_schema_version=PI_HISTORY_VERSION,resume_safe=history['stop_confirmed'])
        try:
            with httpx.Client(timeout=10, follow_redirects=False, headers=self.headers()) as client:
                try:
                    started = client.post(url+'/runs',json=body)
                    if started.status_code == 429:
                        raise RuntimeExecutionError('Pi并发容量已满，未启动诊断')
                    started.raise_for_status()
                    deadline = time.monotonic() + 190
                    while time.monotonic() < deadline:
                        if token.cancelled and not cancel_sent:
                            client.post(f'{url}/runs/{run_id}/cancel').raise_for_status(); cancel_sent=True
                        response = client.get(f'{url}/runs/{run_id}'); response.raise_for_status(); snapshot=response.json()
                        if snapshot.get('scope') != body['scope'] or snapshot.get('run_id') != run_id or snapshot.get('engine_version') != PI_VERSION:
                            raise RuntimeExecutionError('Pi返回身份或版本不匹配')
                        last_snapshot=snapshot
                        if deps.performance: deps.performance.pi_snapshot(snapshot.get("performance"),body["limits"])
                        display = snapshot.get('answer_stream') or {}
                        progress = opts.get('on_progress')
                        if progress and display.get('sequence', -1) > answer_sequence:
                            for item in display.get('messages', []):
                                offset = answer_lengths.get(item['id'], 0)
                                text = item['text']
                                if len(text) > offset:
                                    progress({'kind':'answer_delta','message_id':item['id'],'text':text[offset:]})
                                    answer_lengths[item['id']] = len(text)
                                if item.get('state') in ('process','generated'):
                                    progress({'kind':'answer_process' if item['state']=='process' else 'answer_end','message_id':item['id']})
                            answer_sequence = display['sequence']
                        for event in snapshot.get('events', []):
                            if event['sequence'] <= sequence: continue
                            sequence=event['sequence']
                            if request.on_event:
                                try: request.on_event(RuntimeEvent(**event))
                                except Exception as exc: raise RuntimeExecutionError('Pi事件未能持久化，停止运行') from exc
                            progress = opts.get('on_progress')
                            if event['kind'] in ('tool_started','tool_finished') and progress:
                                try: progress(event['payload'])
                                except Exception: log.exception('Pi progress observer failed')
                            phase = opts.get('on_phase')
                            if phase:
                                try:
                                    if event['kind']=='tool_started': phase('tool_exec')
                                    elif event['kind'] in ('phase','tool_finished'): phase('model_request')
                                except Exception: log.exception('Pi phase observer failed')
                        if snapshot['status'] not in ('running','stopping'):
                            settled=True
                            if snapshot['status']=='failed':
                                if snapshot.get('error_code')=='budget_exceeded': raise RuntimeBudgetExceeded('Pi用量达到限制，未跨引擎重跑', partial_result=result_of(snapshot))
                                raise RuntimeExecutionError('Pi运行失败：'+snapshot.get('error_code','unknown'), partial_result=result_of(snapshot))
                            return result_of(snapshot)
                        token._event.wait(.1) if isinstance(token, PiCancellation) and not token.cancelled else time.sleep(.1)
                    raise RuntimeExecutionError('Pi运行查询超时，结果未知')
                finally:
                    if not settled:
                        token.cancel()
                        try: client.post(f'{url}/runs/{run_id}/cancel')
                        except Exception: log.warning('Pi cancellation acknowledgement unavailable')
        except (RuntimeExecutionError, RuntimeBudgetExceeded) as exc:
            if exc.partial_result is None and last_snapshot is not None:
                last_snapshot['stop_confirmed']=False
                last_snapshot['usage'].update(usage_complete=False,attempt_usage_complete=False,gap_reason='pi_execution_state_unknown')
                exc.partial_result=result_of(last_snapshot)
            if exc.partial_result is None:
                exc.partial_result = RuntimeResult('', 'pi', 0, run_id=run_id, conversation_id=run_id,
                    engine='pi', engine_version=PI_VERSION,
                    usage_json=json.dumps({'usage_complete':False,'cost_complete':False,'gap_reason':'pi_execution_state_unknown','price_table_version':'pi-unpriced@1'}))
            raise
        except Exception as exc:
            if last_snapshot is not None:
                last_snapshot['stop_confirmed']=False
                last_snapshot['usage'].update(usage_complete=False,attempt_usage_complete=False,gap_reason='pi_execution_state_unknown')
                raise RuntimeExecutionError('Pi通信失败，执行停止未确认', partial_result=result_of(last_snapshot)) from exc
            raise RuntimeExecutionError('Pi服务通信失败，未跨引擎重跑', partial_result=RuntimeResult('', 'pi', 0, run_id=run_id, conversation_id=run_id, engine='pi', engine_version=PI_VERSION, usage_json=json.dumps({'usage_complete':False,'cost_complete':False,'gap_reason':'pi_execution_state_unknown'}))) from exc
        finally:
            remove_binding(run_id)

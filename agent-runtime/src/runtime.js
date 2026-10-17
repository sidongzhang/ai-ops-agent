import { Agent } from '@earendil-works/pi-agent-core';
import { loadPrice, estimateCost } from './pricing.js';
import { streamSimple } from '@earendil-works/pi-ai/api/openai-completions';

export const ENGINE_VERSION = 'pi-agent-core@1.2.0+pi-ai@1.2.0';
export const READ_TOOLS = new Set(['list_services','check_service','check_container_state','read_logs','search_logs','query_prometheus','run_kafka_command','run_redis_command','search_knowledge_base']);

export function profileFromEnv(env = process.env) {
  const baseUrl = env.PI_MODEL_BASE_URL;
  const id = env.PI_MODEL_ID;
  if (!baseUrl || !id || !env.PI_MODEL_API_KEY) throw new Error('Pi model profile not configured');
  const url = new URL(baseUrl);
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Invalid model endpoint');
  const model = { id, name: id, api: 'openai-completions', provider: 'aiops', baseUrl,
    reasoning: false, input: ['text'], cost: {input:0,output:0,cacheRead:0,cacheWrite:0},
    contextWindow: 32768, maxTokens: 4096,
    compat: {supportsStore:false, supportsDeveloperRole:false, supportsReasoningEffort:false} };
  return { model, price:loadPrice(env,id), streamFn: (m,c,o) => streamSimple(m,c,{...o,apiKey:env.PI_MODEL_API_KEY,maxRetries:0,maxTokens:4096}) };
}

export function validateStart(body) {
  const allowed = new Set(['run_id','scope','question','system_prompt','tools','gateway_token','model_profile','limits','checkpoint']);
  if (!body || typeof body !== 'object' || Object.keys(body).some(k => !allowed.has(k))) throw new Error('invalid_start');
  if (!/^[a-zA-Z0-9_-]{1,100}$/.test(body.run_id) || body.model_profile !== 'default' ||
      typeof body.question !== 'string' || !body.question.trim() || typeof body.system_prompt !== 'string' ||
      !body.scope || Object.keys(body.scope).sort().join(',') !== 'org_id,system_id' ||
      !Number.isSafeInteger(body.scope.org_id) || body.scope.org_id < 0 || !Number.isSafeInteger(body.scope.system_id) || body.scope.system_id < 0 ||
      typeof body.gateway_token !== 'string' || body.gateway_token.length < 32 || !Array.isArray(body.tools) || body.tools.length > READ_TOOLS.size) throw new Error('invalid_start');
  if(body.checkpoint && (body.checkpoint.format!=='aiops-pi-v2' || body.checkpoint.stop_confirmed!==true ||
      !Array.isArray(body.checkpoint.messages) || !body.checkpoint.messages.length ||
      (body.checkpoint.scope?.org_id!==body.scope.org_id || body.checkpoint.scope?.system_id!==body.scope.system_id))) throw new Error('invalid_checkpoint');
  const names = new Set();
  for (const tool of body.tools) {
    if (!READ_TOOLS.has(tool.name) || names.has(tool.name) || tool.risk !== 'read' || !tool.input_schema ||
        tool.input_schema.type !== 'object' || tool.input_schema.additionalProperties !== false ||
        ['org_id','system_id','scope','run_id'].some(k => k in (tool.input_schema.properties || {}))) throw new Error('invalid_tool');
    names.add(tool.name);
  }
  const l = body.limits;
  if (!l || Object.keys(l).sort().join(',') !== 'requests,tokens,tools' || !['requests','tokens','tools'].every(k => Number.isSafeInteger(l[k]) && l[k] > 0) || l.requests > 12 || l.tools > 24 || l.tokens > 100000) throw new Error('invalid_limits');
}

export class PiRun {
  constructor(body, profile, gatewayBase, fetcher = fetch) {
    validateStart(body);
    this.id = body.run_id; this.scope = body.scope; this.state = 'running'; this.events = []; this.answerMessages = []; this.answerSequence = 0;
    this.performance = []; this.modelStarted = null; this.performanceStart = performance.now();
    this.requests = 0; this.toolCount = 0; this.started = Date.now(); this.errorCode = '';
    this.gatewayBase=gatewayBase;this.fetcher=fetcher;this.profile = profile; this.body = body; this.stopConfirmed=false;this.requiresDrain=false;
    this.baseline=body.checkpoint?.usage || {};this.conversationId=body.checkpoint?.conversation_id || this.id;
    if(body.checkpoint && body.checkpoint.model!==profile.model.id) throw new Error('checkpoint_model_mismatch');
    const history=structuredClone(body.checkpoint?.messages || []);
    if(history[0]?.role==='system') history[0].content=body.system_prompt;
    this.initialLength=history.length;
    this.checkpoint=()=>{if(this.onChange && !this.persistenceFailed) {
      try {this.onChange(this.snapshot());} catch {this.persistenceFailed=true;this.errorCode='persistence_failed';this.agent.abort();}
    }};
    const emit = (kind,payload={}) => this.events.push({run_id:this.id,sequence:this.events.length+1,kind,timestamp:new Date().toISOString(),payload,schema_version:1});this.checkpoint();
    this.emit = emit;
    const tools = body.tools.map(t => ({name:t.name,label:t.name,description:t.description || t.name,parameters:t.input_schema,
      execute: async (callId,args,signal) => {
        let response;try {response = await fetcher(`${gatewayBase}/internal/agent-tools/runs/${this.id}/calls`, {
          method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${body.gateway_token}`},
          body:JSON.stringify({call_id:callId,tool:t.name,arguments:args}),
          signal:AbortSignal.any([signal || new AbortController().signal,AbortSignal.timeout(65000)]),redirect:'error'});}catch(error){this.requiresDrain=true;throw error;}
        if (!response.ok) {
          this.requiresDrain=true;
          const error = await response.json().catch(()=>({}));
          this.errorCode ||= error.detail === 'run_cancelled' ? 'cancelled' : 'gateway_failed';
          this.agent.abort(); throw new Error('Tool gateway failed');
        }
        const result = await response.json();
        if(['timeout','interrupted'].includes(result.status))this.requiresDrain=true;
        if (result.status !== 'success') throw new Error(`Tool ${result.status}: ${result.text}`);
        return {content:[{type:'text',text:result.text}],details:{status:result.status}};
      }}));
    this.agent = new Agent({initialState:{model:profile.model,systemPrompt:body.system_prompt,tools,thinkingLevel:'off',messages:history},
      streamFn:profile.streamFn,toolExecution:'sequential',sessionId:this.conversationId,
      prepareRequest: async () => {
        if (this.requests >= body.limits.requests || this.attemptUsage().total_tokens >= body.limits.tokens) {
          this.errorCode = 'budget_exceeded'; this.agent.abort(); throw new Error('Run usage limit reached');
        }
        this.requests++;
        this.modelStarted = performance.now();
      },
      beforeToolCall: async () => {
        if (++this.toolCount > body.limits.tools) {
          this.errorCode = 'budget_exceeded'; this.agent.abort(); return {block:true,reason:'Tool usage limit reached',terminate:true};
        }
      }});
    this.agent.subscribe(event => {
      if(event.type==='message_update' && event.assistantMessageEvent?.type==='text_delta') {
        const item = this.answerMessages.at(-1);
        if(item) {
          const remaining = 100000 - this.answerMessages.reduce((n,m)=>n+m.text.length,0);
          item.text += event.assistantMessageEvent.delta.slice(0,Math.max(remaining,0));
          this.answerSequence++;
        }
      }
      if(event.type==='message_end' && event.message.role==='assistant' && this.modelStarted!==null) {
        this.performance.push({id:`model-${this.requests}`,start_ms:this.modelStarted-this.performanceStart,
          duration_ms:performance.now()-this.modelStarted,status:['error','aborted'].includes(event.message.stopReason)?(event.message.stopReason==='aborted'?'cancelled':'failed'):'success'});
        this.modelStarted=null;
      }
      if(event.type==='message_end') {
        const item = this.answerMessages.at(-1);
        if(event.message.role==='assistant' && item) {
          item.state = event.message.stopReason==='toolUse' ? 'process' : 'generated';
          this.answerSequence++;
        }
        this.checkpoint();
      }
      if (event.type === 'tool_execution_start') emit('tool_started',{kind:'tool_start',call_id:event.toolCallId,tool:event.toolName,input:event.args});
      if (event.type === 'tool_execution_end') emit('tool_finished',{kind:'tool_end',call_id:event.toolCallId,tool:event.toolName,
        status:event.isError?'error':'success',duration_ms:0,output:(event.result?.content || []).filter(c=>c.type==='text').map(c=>c.text).join('\n').slice(0,1500)});
      if (event.type === 'message_start' && event.message.role === 'assistant') {
        if(this.answerMessages.length < 128) this.answerMessages.push({id:`pi-${this.requests}-${this.answerMessages.length}`,text:'',state:'generating'});
        this.answerSequence++;
        emit('phase',{phase:'model_request'});
      }
    });
    emit('run_started',{engine:'pi',...this.scope});
  }
  cancel(reason='cancelled') {
    if (!['running','stopping'].includes(this.state)) return;
    if (!this.errorCode) this.errorCode = reason;
    this.state='stopping'; this.emit('run_stopping',{reason:this.errorCode});this.agent.abort();
  }
  async execute(deadlineMs=120000) {
    const timer = setTimeout(()=>this.cancel('deadline_exceeded'),deadlineMs);
    try {
      await this.agent.prompt(this.body.question);
      const last = this.agent.state.messages.filter(m=>m.role==='assistant').at(-1);
      if (!this.errorCode && last?.stopReason === 'error') this.errorCode = 'model_failed';
      if (!this.errorCode && last?.stopReason === 'aborted') this.errorCode = 'cancelled';
      if (!this.errorCode && last?.stopReason === 'length') this.errorCode = 'output_truncated';
      this.state = this.errorCode === 'cancelled' ? 'cancelled' : this.errorCode ? 'failed' : 'success';
    } catch { this.state = this.errorCode === 'cancelled' ? 'cancelled' : 'failed'; this.errorCode ||= 'model_failed'; }
    finally {
      clearTimeout(timer);
      if(this.modelStarted!==null) {
        this.performance.push({id:`model-${this.requests}`,start_ms:this.modelStarted-this.performanceStart,duration_ms:null,status:'unknown'});
        this.modelStarted=null;
      }
      // A disconnected fetch is not proof that the Python executor has finished.
      if(this.requiresDrain) {
        this.state='stopping';this.emit('run_stopping',{reason:this.errorCode});
        const end=Date.now()+60000;
        while(Date.now()<end) {
          try {
            const response=await this.fetcher(`${this.gatewayBase}/internal/agent-tools/runs/${this.id}/status`,{headers:{Authorization:`Bearer ${this.body.gateway_token}`},signal:AbortSignal.timeout(2000)});
            if(response.ok){const status=await response.json();if(status.active===0 && status.stop_confirmed!==false){this.stopConfirmed=true;break;}}
          } catch {}
          await new Promise(r=>setTimeout(r,100));
        }
      } else this.stopConfirmed=true;
      if(!this.stopConfirmed){this.errorCode='stop_unconfirmed';this.state='failed';}
      else this.state=this.errorCode==='cancelled'?'cancelled':this.errorCode?'failed':'success';
      if(this.persistenceFailed){this.state='failed';this.stopConfirmed=false;this.requiresDrain=false;}
      this.emit('runtime_settled',{status:this.state,error_code:this.errorCode,stop_confirmed:this.stopConfirmed});
      delete this.body.gateway_token;
    }
  }
  attemptUsage() {
    const msgs=this.agent.state.messages.slice(this.initialLength).filter(m=>m.role==='assistant');
    const u={input_tokens:0,output_tokens:0,cache_read_tokens:0,cache_write_tokens:0,total_tokens:0,requests:this.requests,tool_calls:this.toolCount};
    for(const m of msgs){const x=m.usage||{};u.input_tokens+=(x.input||0)+(x.cacheRead||0)+(x.cacheWrite||0);u.output_tokens+=x.output||0;
      u.cache_read_tokens+=x.cacheRead||0;u.cache_write_tokens+=x.cacheWrite||0;u.total_tokens+=x.totalTokens||0;}
    u.cost=estimateCost(u,this.profile.price);
    u.cost_complete=Number.isFinite(u.cost);
    u.usage_complete=msgs.length===this.requests && msgs.every(m=>!['aborted','error'].includes(m.stopReason) && m.usage?.totalTokens>0) && u.cost_complete;
    u.gap_reason=!msgs.length || !u.total_tokens?'provider_no_usage':msgs.some(m=>['aborted','error'].includes(m.stopReason))?'in_flight_request_aborted':!u.cost_complete?'price_not_configured':'';
    u.price_table_version=this.profile.price?.version || 'pi-unpriced@1';return u;
  }
  usage() {
    const current=this.attemptUsage(),u={...current};
    for(const k of ['input_tokens','output_tokens','cache_read_tokens','cache_write_tokens','total_tokens','requests','tool_calls'])u[k]+=(this.baseline[k]||0);
    u.attempt_cost=current.cost;u.attempt_usage_complete=current.usage_complete;
    u.cost=Number.isFinite(this.baseline.cost || 0) && (this.baseline.cost!==null) && current.cost!==null?(this.baseline.cost||0)+current.cost:null;
    u.usage_complete=current.usage_complete && this.baseline.usage_complete!==false;
    return u;
  }
  snapshot() {
    const messages = this.agent.state.messages;
    const last=messages.filter(m=>m.role==='assistant').at(-1);
    return {run_id:this.id,scope:this.scope,conversation_id:this.conversationId,stop_confirmed:this.stopConfirmed,status:this.state,error_code:this.errorCode,engine:'pi',engine_version:ENGINE_VERSION,
      performance:Date.now()<this.started+7*86400000?{expires_at:this.started+7*86400000,models:this.modelStarted===null?this.performance:[...this.performance,{id:`model-${this.requests}`,start_ms:this.modelStarted-this.performanceStart,duration_ms:null,status:'running'}],request_used:this.requests,request_baseline:this.baseline.requests||0,tool_used:this.toolCount,tool_baseline:this.baseline.tool_calls||0}:undefined,
      history_schema_version:2,answer_stream:{sequence:this.answerSequence,messages:this.answerMessages},events:this.events,model:this.profile.model.id,duration_ms:Date.now()-this.started,
      answer:this.state==='success'?(last?.content||[]).filter(c=>c.type==='text').map(c=>c.text).join('\n'):'',
      messages,usage:this.usage()};
  }
}

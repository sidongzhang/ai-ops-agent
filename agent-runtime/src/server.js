import { RunStore } from './store.js';
import http from 'node:http';
import { timingSafeEqual, createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { ENGINE_VERSION, PiRun, profileFromEnv, validateStart } from './runtime.js';

function sameSecret(value, expected) {
  const hash = x => createHash('sha256').update(x || '').digest();
  return Boolean(expected && timingSafeEqual(hash(value),hash(`Bearer ${expected}`)));
}
export function createServer({secret,gatewayBase,profile,maxConcurrent=2,maxRetained=100,deadlineMs=120000,store=new RunStore()}={}) {
  if (!secret || secret.length < 32 || !gatewayBase || !profile) throw new Error('Pi service configuration missing');
  const url = new URL(gatewayBase);
  if (!['http:','https:'].includes(url.protocol) || !['127.0.0.1','localhost','[::1]'].includes(url.hostname) || url.username || url.password || url.search || url.pathname !== '/') throw new Error('Gateway must be a fixed loopback origin');
  const runs = new Map(); let stopping=false;
  const server=http.createServer(async (req,res)=>{
    const send=(code,body)=>{res.writeHead(code,{'Content-Type':'application/json'});res.end(JSON.stringify(body));};
    if (!sameSecret(req.headers.authorization,secret)) return send(401,{error:'unauthorized'});
    const path=new URL(req.url,'http://localhost').pathname;
    if (path==='/healthz' && req.method==='GET') return send(200,{status:stopping?'stopping':'ok',engine_version:ENGINE_VERSION,node_version:process.versions.node,protocol_version:2,active:[...runs.values()].filter(r=>['running','stopping'].includes(r.state)).length});
    try {
      if (path==='/runs' && req.method==='POST') {
        if (stopping) return send(503,{error:'stopping'});
        let size=0;const chunks=[];
        for await (const chunk of req) {size+=chunk.length;if(size>256*1024) return send(413,{error:'too_large'});chunks.push(chunk);}
        const body=JSON.parse(Buffer.concat(chunks)); validateStart(body);
        const old=runs.get(body.run_id);const saved=store.get(body.run_id);
        const digest=createHash('sha256').update(JSON.stringify(body)).digest('hex');
        if(saved && !old) return saved.digest===digest?send(200,{run_id:body.run_id,status:saved.snapshot.status}):send(409,{error:'idempotency_conflict'});
        if(old) return old.digest===digest?send(200,{run_id:old.id,status:old.state}):send(409,{error:'idempotency_conflict'});
        if([...runs.values()].filter(r=>['running','stopping'].includes(r.state)).length>=maxConcurrent) return send(429,{error:'capacity'});
        if(runs.size>=maxRetained) {
          const terminal=[...runs.values()].find(r=>!['running','stopping'].includes(r.state));
          if(!terminal) return send(429,{error:'retention_capacity'});
          runs.delete(terminal.id);
        }
        const run=new PiRun(body,profile,gatewayBase);run.digest=digest;run.onChange=snapshot=>store.save(snapshot,digest);store.save(run.snapshot(),digest);runs.set(run.id,run);
        run.promise=run.execute(deadlineMs);return send(202,{run_id:run.id,status:run.state});
      }
      const match=path.match(/^\/runs\/([a-zA-Z0-9_-]+)(\/cancel)?$/);
      if(match) {
        const run=runs.get(match[1]);
        if (!match[2] && req.method==='DELETE') {
          const saved=store.get(match[1]);const snapshot=run?.snapshot() || saved?.snapshot;
          if (!snapshot) return send(200,{deleted:false});
          const query=new URL(req.url,'http://localhost').searchParams;
          if (String(snapshot.scope.org_id)!==query.get('org_id') || String(snapshot.scope.system_id)!==query.get('system_id')) return send(404,{error:'unknown_run'});
          if (['running','stopping'].includes(snapshot.status)) return send(409,{error:'run_active'});
          store.delete(match[1]);runs.delete(match[1]);return send(200,{deleted:true});
        }
        if(!run){const saved=store.get(match[1]);if(saved && !match[2] && req.method==='GET')return send(200,saved.snapshot);return send(404,{error:'unknown_run'});}
        if(match[2] && req.method==='POST') {run.cancel();return send(200,{run_id:run.id,status:run.state});}
        if(!match[2] && req.method==='GET') return send(200,run.snapshot());
      }
      return send(404,{error:'not_found'});
    }catch {return send(400,{error:'invalid_request'});}
  });
  const telemetryCleanup=setInterval(()=>{for(const run of runs.values())if(Date.now()>=run.started+7*86400000)run.performance=[];},3600000);telemetryCleanup.unref();
  server.requestTimeout=15000;server.headersTimeout=10000;
  server.stop=async()=>{clearInterval(telemetryCleanup);stopping=true;for(const r of runs.values())r.cancel();await Promise.all([...runs.values()].map(r=>r.promise));await new Promise(resolve=>server.close(resolve));store.close();};
  return server;
}
if(import.meta.url===pathToFileURL(process.argv[1]||'').href) {
  const server=createServer({secret:process.env.PI_SERVICE_SECRET,gatewayBase:process.env.PI_TOOL_GATEWAY_ORIGIN,
    profile:profileFromEnv(),maxConcurrent:2,deadlineMs:120000,store:new RunStore(process.env.PI_STATE_PATH || 'pi-runs.db')});
  server.listen(Number(process.env.PI_PORT||8810),'127.0.0.1',()=>console.log('Pi runtime listening on loopback'));
  for(const signal of ['SIGTERM','SIGINT'])process.on(signal,()=>{
    const timer=setTimeout(()=>process.exit(1),5000);server.stop().then(()=>{clearTimeout(timer);process.exit(0);});
  });
}

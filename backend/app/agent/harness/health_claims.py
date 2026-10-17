"""Bounded explicit health claims; not a semantic or whole-answer verifier."""
import re
from datetime import datetime, timezone
from .freshness import ttl_seconds


class Unresolved(Exception):
    def __init__(self, status): self.status = status


def stamp(value):
    try:
        result = datetime.fromisoformat(value)
        return result if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def observations(calls):
    rows = []
    for call in calls:
        if not isinstance(call, dict) or call.get('tool') not in ('list_services', 'check_service') or call.get('status') != 'success' or not call.get('call_id'):
            continue
        body = str(call.get('output') or '')
        if '本次诊断已查过相同参数' in body: continue
        data = call.get('health_observation')
        if isinstance(data, dict) and data.get('version') == 1:
            rows.append({**data, 'call_id': call['call_id'], 'tool': call['tool'], 'scope_identity': call.get('scope')})
            continue
        # Existing native histories have text + original result timestamps.
        # Parse only explicit connector rows, never infer complete registration.
        entries = []
        for line in body.splitlines():
            match = re.match(r'^\s*(.+?):\s*(.*?)\s*([✅❌])\s*$', line)
            if match:
                http = re.search(r'\bHTTP\s+([1-5][0-9]{2})(?!\d)', match[2])
                entries.append({'service': match[1], 'ok': match[3] == '✅', 'http_status': int(http[1]) if http else None})
        if entries:
            rows.append({'services': entries, 'scope': 'legacy_unknown', 'complete': False,
                         'registered_services': None, 'observed_at': call.get('observed_at'),
                         'tool': call['tool'], 'call_id': call['call_id'], 'scope_identity': call.get('scope')})
    return rows


def check_health_claims(answer, calls, *, now=None):
    now = now or datetime.now(timezone.utc)
    rows = observations(calls)
    text = re.sub(r'[*`]', '', answer[:20000])
    checks = []
    def add(claim, status, observed=None, sources=()):
        checks.append({'kind': claim['kind'], 'subject': claim.get('subject', '服务探活'),
            'claim': claim['text'], 'claimed': claim.get('value'), 'status': status,
            'observed_values': [] if observed is None else [str(observed)],
            'tool_call_ids': list(dict.fromkeys(r['call_id'] for r in sources)),
            'observations': [{'call_id':r['call_id'], 'observed_at':r.get('observed_at'), 'scope':r.get('scope'), 'services':r.get('services', [])} for r in {r['call_id']:r for r in sources}.values()]})
    def fresh(row):
        dt = stamp(row.get('observed_at'))
        return dt is not None and 0 <= (now-dt).total_seconds() <= ttl_seconds(row['tool'])
    def latest(candidates):
        if not candidates: return [], 'unavailable'
        if any(stamp(r.get('observed_at')) is None for r in candidates):
            # A newer undated observation cannot be ordered against dated results.
            return candidates, 'stale_or_undated'
        identities = {str(r.get('scope_identity')) for r in candidates if r.get('scope_identity')}
        if len(identities) > 1:
            # run_id changes across resumes; only org/system define object scope.
            identities = {(r['scope_identity'].get('org_id'), r['scope_identity'].get('system_id')) for r in candidates if isinstance(r.get('scope_identity'),dict)}
            if len(identities)>1: return candidates, 'ambiguous'
        newest = max(stamp(r['observed_at']) for r in candidates)
        selected = [r for r in candidates if stamp(r['observed_at']) == newest]
        return selected, '' if all(fresh(r) for r in selected) else 'stale_or_undated'
    def compare(claim, candidates, getter, support=lambda: [], missing_status='scope_insufficient'):
        chosen, error = latest(candidates)
        if error: add(claim,error,sources=chosen);return
        try:
            values = [getter(r) for r in chosen]
        except Unresolved as exc:
            add(claim, exc.status, sources=chosen + support())
            return
        if any(v is None for v in values): add(claim,missing_status,sources=chosen + support());return
        if len(set(values)) != 1: add(claim,'ambiguous',sources=chosen);return
        add(claim,'matches_tool_text' if values[0] == claim['value'] else 'conflicts_tool_text',values[0],chosen + support())
    # Explicit numerals only; no promise of recognizing every Chinese expression.
    clauses = re.split(r'[。；\n]', text)
    factual = '\n'.join(c for c in clauses if not re.search(r'[?？]|(?:是否|假如|如果|例如|比如|若|不能|无法|不代表|不等于|尚未)|(?:建议|请|应该|计划|需要|可以).{0,12}(?:检查|探活)', c))
    text = factual
    claims = []
    patterns = [
        ('checked_count',r'(?:本次|本轮)(?:实际)?(?:检查|探活)(?:服务)?(?:数量|总数|数)?\s*(?:为|是|[:：=])\s*(\d+)'),
        ('healthy_count',r'(?<![\w])(?:正常|健康|探活通过)(?:数量|总数|数)?\s*(?:为|是|[:：=])\s*(\d+)'),
        ('unhealthy_count',r'(?<![\w])(?:异常|不健康|探活失败)(?:数量|总数|数)?\s*(?:为|是|[:：=])\s*(\d+)'),
        ('registered_count',r'(?:已注册|注册的?|登记的?)(?:服务)?(?:总数|数量)?\s*(?:为|是|共|共有|[:：=])?\s*(\d+)\s*(?:个|项)(?:已注册的?)?服务'),
        ('registered_count',r'(?:已注册|注册|登记)(?:的)?服务(?:总数|数量|数)?\s*(?:为|是|共|共有|[:：=])\s*(\d+)'),
        ('registered_count',r'(\d+)\s*(?:个|项)\s*(?:已注册|注册|登记)(?:的)?服务'),
        ('checked_count',r'(?:本次|本轮)?(?:实际)?(?:检查|探活)(?:了|的)?\s*(?:共|全部|总计)?\s*(\d+)\s*(?:个|项)(?:注册)?服务'),
        ('checked_count',r'(?:全部|共有|共计|共)\s*(\d+)\s*(?:个|项)(?:注册)?服务'),
        ('checked_count',r'(\d+)\s*(?:个|项)服务(?:探活)?(?:全部|全|均|都)(?:探活)?(?:正常|健康|通过)'),
        ('healthy_count',r'(\d+)\s*(?:个|项)(?:服务)?(?:全部|均|都)(?:正常|健康|探活通过)'),
        ('healthy_count',r'(\d+)\s*(?:个|项)服务(?:当前)?(?:正常|健康|探活通过)'),
        ('healthy_count',r'(?:正常|健康|探活通过)(?:的)?服务(?:数量|数)?\s*(?:为|是|有|共|[:：=])\s*(\d+)'),
        ('unhealthy_count',r'(\d+)\s*(?:个|项)服务(?:异常|不健康|探活失败)'),
        ('unhealthy_count',r'(?:异常|不健康|探活失败)(?:的)?服务(?:数量|数)?\s*(?:为|是|有|共|[:：=])\s*(\d+)')]
    for kind,pattern in patterns:
        for match in re.finditer(pattern,text):
            claims.append({'kind':kind,'text':match[0],'value':int(match[1])})
    for claim in claims[:32]:
        # Counts require a known aggregate scope; one-service probes cannot prove totals.
        candidates = [r for r in rows if r.get('scope') == 'registered_services']
        if not candidates:
            add(claim,'scope_insufficient' if rows else 'unavailable',sources=rows);continue
        count_sources = []
        def count(row):
            names=row.get('registered_services')
            services=row.get('services',[])
            if claim['kind']=='registered_count': return len(names) if isinstance(names,list) and len(names)==len(set(names)) else None
            if claim['kind']=='checked_count': return len(services) if len({s['service'] for s in services})==len(services) else None
            if not row.get('complete'): return None
            latest_values=[]
            for name in names:
                chosen, problem = latest([r for r in rows if any(s.get('service') == name for s in r['services'])])
                count_sources.extend(chosen)
                if problem: raise Unresolved(problem)
                values={s.get('ok') for r in chosen for s in r['services'] if s.get('service')==name}
                if len(values)!=1 or None in values: raise Unresolved('ambiguous')
                latest_values.append(values.pop())
            return sum(ok is (claim['kind']=='healthy_count') for ok in latest_values)
        compare(claim,candidates,count,lambda: count_sources)
    # Match service names exactly against observed names, including spaces and Chinese.
    names={s.get('service') for r in rows for s in r.get('services',[]) if s.get('service')}
    matched_http = set()
    for name in sorted(names,key=len,reverse=True):
        pattern = re.compile(r'(?<![\w.-])'+re.escape(name)+r'\s*[:：]?\s*(?:的\s*)?(?:返回\s*)?HTTP\s*(?:(?:健康)?检查\s*)?(?:(?:状态码?|返回)\s*[为是=:：]?\s*)?([1-5][0-9]{2})(?!\d)',re.I)
        for match in pattern.finditer(text):
            matched_http.add((match.start(),match.end()))
            claim={'kind':'http_status','subject':name,'text':match[0],'value':int(match[1])}
            candidates=[r for r in rows if any(s.get('service')==name for s in r['services'])]
            def http_value(row):
                values = {s.get('http_status') for s in row['services'] if s.get('service') == name}
                if len(values) != 1: raise Unresolved('ambiguous')
                return values.pop()
            compare(claim,candidates,http_value,missing_status='unavailable')
    for match in re.finditer(r'(?<![\w.-])([A-Za-z_][\w.-]*)\s+HTTP\s+([1-5][0-9]{2})(?!\d)',text):
        if not any(a<=match.start() and b>=match.end() for a,b in matched_http):
            add({'kind':'http_status','subject':match[1],'text':match[0],'value':int(match[2])},'unavailable')
    # Check declarative, explicit whole-service statements; skip questions/negations.
    for clause in re.split(r'[。；\n]',text):
        if re.search(r'[?？]|(?:是否|如果|若|不能|无法|不代表|不等于|尚未)',clause): continue
        business = re.search(r'(?:整个|全部)?(?:业务|系统)(?:当前|现在)?(?:完全)?(?:没有|无|不存在)(?:任何)?(?:故障|异常|问题)',clause)
        all_ok = re.search(r'(?:全部|所有)(?:的)?(?:\s*\d+\s*(?:个|项))?(?:已注册的?|注册的?)?服务(?:当前|现在|的探活|探活)?(?:均|都|全部|全)?(?:正常|健康|通过)',clause)
        all_ok = all_ok or re.search(r'服务[，,：:\s]*(?:\d+\s*(?:个|项))?(?:探活)?(?:全部|全|均|都)(?:探活)?(?:正常|健康|通过)',clause)
        if business:
            add({'kind':'business_health','text':business[0]},'scope_insufficient',sources=rows)
        if all_ok:
            claim={'kind':'all_services_healthy','text':all_ok[0],'value':True}
            candidates=[r for r in rows if r.get('scope')=='registered_services']
            chosen,error=latest(candidates)
            if error: add(claim, 'scope_insufficient' if error == 'unavailable' and rows else error, sources=chosen or rows);continue
            if any(not r.get('complete') or not r.get('registered_services') for r in chosen):add(claim,'scope_insufficient',sources=chosen);continue
            # Later individual probes update the aggregate only when dates are ordered.
            latest_services={};sources=list(chosen);problem=''
            for name in chosen[0]['registered_services']:
                eligible=[r for r in rows if any(s.get('service')==name for s in r['services'])]
                selected,issue=latest(eligible)
                if issue:problem=issue;break
                values={s.get('ok') for r in selected for s in r['services'] if s.get('service')==name}
                if len(values)!=1 or None in values:problem='ambiguous';break
                latest_services[name]=values.pop();sources.extend(selected)
            if problem:add(claim,problem,sources=sources)
            else:add(claim,'matches_tool_text' if latest_services and all(latest_services.values()) else 'conflicts_tool_text',f'已检查 {len(latest_services)} 项服务，正常 {sum(latest_services.values())} 项，异常 {len(latest_services)-sum(latest_services.values())} 项',sources)
    unique = {}
    for check in checks:
        unique[(check['kind'], check['subject'], check['claim'])] = check
    return list(unique.values())[:64]

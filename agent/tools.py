"""
Agent 工具集：14 个工具，覆盖进程/容器探活、日志分析、数据库、消息队列、缓存、系统资源。

已从「硬编码操作单套 demo」重构为「按 system + service 经连接器分发」：
每个工具接收 (params, system)，system 是注册表里的系统描述符（registry.get_system）。
连接信息（MySQL/Prometheus/Kafka/Redis）从 system['infra'] 取，而非模块级全局。
写动作（restart_service / restart_docker）仅对 local=true 的平台托管系统开放。
"""
import os
import sys
import socket
import subprocess
import logging
import concurrent.futures

import requests
import mysql.connector
from mysql.connector import Error as MySQLError

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from registry import get_system  # noqa: E402
from connectors import get_connector  # noqa: E402

logger = logging.getLogger(__name__)

LOGS_DIR = os.path.join(BASE_DIR, 'logs')
PIDS_DIR = os.path.join(BASE_DIR, 'pids')

# 仅作为系统未声明 infra 时的兜底默认值
_PROMETHEUS_URL_ENV = os.getenv('PROMETHEUS_URL', 'http://localhost:9090')


# ── 内部辅助 ──────────────────────────────────────────────────────────────────

def _svc(system: dict, name: str):
    for s in system.get('services', []):
        if s.get('name') == name:
            return s
    return None


def _names(system: dict) -> str:
    return ', '.join(s.get('name', '') for s in system.get('services', [])) or '（无）'


def _service_name(params: dict) -> str:
    return params.get('service') or params.get('service_name') or ''


def _infra(system: dict, key: str) -> dict:
    return (system.get('infra', {}) or {}).get(key, {}) or {}


def collect_health(system: dict) -> list:
    """并发采集系统内所有服务的健康状态，返回 [(name, ok, detail), ...]。供 list_services 和定时巡检复用。"""
    services = system.get('services', [])
    if not services:
        return []

    def _one(svc):
        try:
            ok, detail = get_connector(svc, system).health()
        except Exception as e:
            ok, detail = False, f'检查失败: {e}'
        return (svc.get('name', ''), ok, detail)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(services))) as ex:
        return list(ex.map(_one, services))


# ── 工具实现（统一签名 (params, system)）──────────────────────────────────────

def list_services(params: dict, system: dict) -> str:
    health = collect_health(system)
    if not health:
        return f"系统 {system.get('id', '')} 未注册任何服务"
    title = f"=== {system.get('name') or system.get('id')} 服务状态 ==="
    lines = [f"  {name:14s}: {detail} {'✅' if ok else '❌'}" for name, ok, detail in health]
    return title + '\n' + '\n'.join(lines)


def check_process(params: dict, system: dict) -> str:
    name = _service_name(params)
    svc = _svc(system, name)
    if not svc:
        return f'系统 {system.get("id")} 中无服务「{name}」，可选: {_names(system)}'
    ok, detail = get_connector(svc, system).health()
    return f"服务 {name}: {detail} {'✅' if ok else '❌'}"


def read_logs(params: dict, system: dict) -> str:
    name = _service_name(params)
    lines = int(params.get('lines', 50) or 50)
    svc = _svc(system, name)
    if not svc:
        return f'系统 {system.get("id")} 中无服务「{name}」，可选: {_names(system)}'
    return get_connector(svc, system).read_logs(lines)


def search_logs(params: dict, system: dict) -> str:
    name = _service_name(params)
    keyword = params.get('keyword', '')
    lines = int(params.get('lines', 200) or 200)
    if not keyword:
        return '请提供要搜索的关键词'
    svc = _svc(system, name)
    if not svc:
        return f'系统 {system.get("id")} 中无服务「{name}」，可选: {_names(system)}'
    return get_connector(svc, system).search_logs(keyword, lines)


def get_metrics(params: dict, system: dict) -> str:
    query = params.get('query', 'up')
    url = _infra(system, 'prometheus').get('url')
    # 仅平台托管系统才回退到本机 Prometheus；远程系统必须显式配置，避免误打平台自己的指标
    if not url and system.get('local'):
        url = _PROMETHEUS_URL_ENV
    if not url:
        return '该系统未配置 Prometheus（infra.prometheus.url），无法查询指标'
    try:
        r = requests.get(f'{url}/api/v1/query', params={'query': query}, timeout=5)
        if r.status_code != 200:
            return f'Prometheus 返回 HTTP {r.status_code}'
        data = r.json().get('data', {}).get('result', [])
        if not data:
            return f"查询 '{query}' 无结果"
        lines = []
        for item in data[:10]:
            metric = item.get('metric', {})
            val = item.get('value', [None, '?'])[1]
            lines.append(f'{metric} => {val}')
        return '\n'.join(lines)
    except Exception as e:
        return f'无法连接 Prometheus: {e}'


def restart_service(params: dict, system: dict) -> str:
    name = _service_name(params)
    svc = _svc(system, name)
    if not svc:
        return f'系统 {system.get("id")} 中无服务「{name}」，可选: {_names(system)}'
    if svc.get('connector', 'local') != 'local':
        return (f'⚠️ 系统「{system.get("id")}」为远程接入（agentless 只读监控），'
                f'当前版本未开放远程重启。请人工处理或联系平台升级写动作权限。')
    conn = get_connector(svc, system)
    if not hasattr(conn, 'restart_process'):
        return f'服务 {name} 不支持重启'
    return conn.restart_process()


def query_database(params: dict, system: dict) -> str:
    sql = params.get('sql', '').strip()
    if not sql.upper().startswith('SELECT'):
        return '安全限制：只允许 SELECT 查询'
    cfg = _infra(system, 'mysql')
    if not cfg:
        return '该系统未配置数据库（infra.mysql）'
    conn_cfg = {
        'host': cfg.get('host', 'localhost'),
        'port': int(cfg.get('port', 3306)),
        'user': cfg.get('user'),
        'password': cfg.get('password'),
        'database': cfg.get('database'),
    }
    try:
        conn = mysql.connector.connect(**conn_cfg)
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        cursor.close()
        conn.close()
        if not rows:
            return '查询结果为空'
        header = ' | '.join(cols)
        sep = '-' * len(header)
        lines = [header, sep] + [' | '.join(str(v) for v in row) for row in rows[:20]]
        if len(rows) > 20:
            lines.append(f'... 共 {len(rows)} 行，只显示前20行')
        return '\n'.join(lines)
    except MySQLError as e:
        return f'数据库查询失败: {e}'


def get_kafka_status(params: dict, system: dict) -> str:
    cfg = _infra(system, 'kafka')
    if not cfg:
        return '该系统未配置 Kafka（infra.kafka）'
    topic = params.get('topic', '')
    hint = cfg.get('container_hint', 'kafka')
    bootstrap = cfg.get('bootstrap', 'localhost:9092')
    try:
        ps = subprocess.run(
            ['docker', 'compose', 'ps', '--format', '{{.Name}}'],
            capture_output=True, text=True, cwd=BASE_DIR, timeout=10
        )
        containers = ps.stdout.strip().splitlines()
        kafka_container = next((c for c in containers if hint.lower() in c.lower()), '')
        if not kafka_container:
            return 'Kafka 容器未运行，请先执行 docker compose up -d'

        if topic:
            r = subprocess.run(
                ['docker', 'exec', kafka_container, '/opt/kafka/bin/kafka-topics.sh',
                 '--bootstrap-server', bootstrap, '--describe', '--topic', topic],
                capture_output=True, text=True, timeout=15
            )
            return r.stdout or r.stderr or f'Topic {topic} 不存在'

        r = subprocess.run(
            ['docker', 'exec', kafka_container, '/opt/kafka/bin/kafka-topics.sh',
             '--bootstrap-server', bootstrap, '--list'],
            capture_output=True, text=True, timeout=15
        )
        topics = r.stdout.strip() or '（无 topic）'
        lag_r = subprocess.run(
            ['docker', 'exec', kafka_container, '/opt/kafka/bin/kafka-consumer-groups.sh',
             '--bootstrap-server', bootstrap, '--describe', '--all-groups'],
            capture_output=True, text=True, timeout=15
        )
        lag_output = lag_r.stdout.strip() or '（无消费者组）'
        return f'=== Kafka Topics ===\n{topics}\n\n=== 消费者 Lag ===\n{lag_output}'
    except subprocess.TimeoutExpired:
        return 'Kafka 查询超时'
    except Exception as e:
        return f'Kafka 查询失败: {e}'


def query_redis(params: dict, system: dict) -> str:
    cfg = _infra(system, 'redis')
    if not cfg:
        return '该系统未配置 Redis（infra.redis）'
    container = cfg.get('container', 'ai-ops-agent-redis-1')
    command = params.get('command', 'INFO').strip()
    allowed = ('INFO', 'DBSIZE', 'TTL', 'TYPE', 'LLEN', 'SCARD', 'ZCARD', 'HLEN',
               'GET', 'KEYS', 'SCAN', 'SMEMBERS', 'LRANGE', 'HGETALL', 'PING')
    cmd_upper = command.upper().split()[0]
    if cmd_upper not in allowed:
        return f'安全限制：只允许只读命令，不支持 {cmd_upper}'
    try:
        r = subprocess.run(
            ['docker', 'exec', '-i', container, 'redis-cli'] + command.split(),
            capture_output=True, text=True, cwd=BASE_DIR, timeout=10
        )
        if r.returncode != 0 and 'Error' in r.stderr:
            r = subprocess.run(
                ['redis-cli'] + command.split(),
                capture_output=True, text=True, timeout=10
            )
        return r.stdout.strip() or r.stderr.strip() or '（无返回）'
    except FileNotFoundError:
        return 'redis-cli 未找到，请确认 Redis 容器正在运行'
    except Exception as e:
        return f'Redis 查询失败: {e}'


def get_system_info(params: dict, system: dict) -> str:
    """主机资源概况。仅对平台托管（local=true）系统有意义；远程系统请走 get_metrics 拉 node_* 指标。"""
    if not system.get('local'):
        return ('该系统为远程接入，无法直接读取主机资源；'
                '请用 get_metrics 查询其 Prometheus 的 node_* 指标（如 node_load1、node_memory_MemAvailable_bytes）。')
    lines = []
    try:
        uptime = subprocess.run(['uptime'], capture_output=True, text=True).stdout.strip()
        lines.append(f'负载: {uptime}')
    except Exception:
        pass
    try:
        top = subprocess.run(['top', '-l', '1', '-n', '0'], capture_output=True, text=True, timeout=10)
        for line in top.stdout.splitlines():
            if 'CPU usage' in line:
                lines.append(f'CPU: {line.strip()}')
                break
    except Exception:
        pass
    try:
        vm = subprocess.run(['vm_stat'], capture_output=True, text=True).stdout
        stats = {}
        for l in vm.splitlines():
            if ':' in l:
                k, v = l.split(':', 1)
                stats[k.strip()] = v.strip().rstrip('.')
        page = 4096
        free = int(stats.get('Pages free', '0').replace(',', '')) * page
        active = int(stats.get('Pages active', '0').replace(',', '')) * page
        wired = int(stats.get('Pages wired down', '0').replace(',', '')) * page
        used = (active + wired) / 1024 / 1024 / 1024
        free_gb = free / 1024 / 1024 / 1024
        lines.append(f'内存: 已用 {used:.1f} GB / 空闲 {free_gb:.1f} GB')
    except Exception:
        pass
    try:
        df = subprocess.run(['df', '-h', '/'], capture_output=True, text=True)
        for line in df.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 5:
                lines.append(f'磁盘(/): 总量 {parts[1]}  已用 {parts[2]} ({parts[4]})')
                break
    except Exception:
        pass
    try:
        ps = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        lines.append(f'进程总数: {len(ps.stdout.strip().splitlines()) - 1}')
    except Exception:
        pass
    return '\n'.join(lines) if lines else '无法获取系统信息'


def check_port(params: dict, system: dict) -> str:
    host = params.get('host', 'localhost')
    port = int(params.get('port', 80))
    timeout = float(params.get('timeout', 3))
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        if result == 0:
            return f'端口 {host}:{port} 可达 ✅'
        return f'端口 {host}:{port} 不可达 ❌ (错误码: {result})'
    except socket.gaierror as e:
        return f'域名解析失败 {host}: {e}'
    except Exception as e:
        return f'端口检测失败: {e}'


def docker_logs(params: dict, system: dict) -> str:
    if not system.get('local'):
        return '该系统为远程接入，无本机 Docker；请用 read_logs（经 ssh/k8s 连接器）查看服务日志。'
    container = params.get('container', '')
    lines = params.get('lines', 50)
    if not container:
        return '请提供容器名称（如 kafka、mysql、redis、prometheus）'
    try:
        r = subprocess.run(
            ['docker', 'compose', 'logs', '--tail', str(lines), '--no-color', container],
            capture_output=True, text=True, cwd=BASE_DIR, timeout=15
        )
        output = r.stdout.strip() or (r.stderr.strip() if r.stderr else '')
        return output or f'容器 {container} 无日志输出'
    except subprocess.TimeoutExpired:
        return f'获取 {container} 日志超时'
    except Exception as e:
        return f'获取容器日志失败: {e}'


def restart_docker(params: dict, system: dict) -> str:
    if not system.get('local'):
        return f'⚠️ 系统「{system.get("id")}」为远程接入，当前版本未开放远程容器重启。'
    container = params.get('container', '')
    # 允许重启的容器 = 本系统里 kind=docker 的本机服务
    allowed = {}
    for s in system.get('services', []):
        if s.get('connector', 'local') == 'local' and s.get('kind') == 'docker':
            allowed[s.get('container', s.get('name'))] = s
            allowed[s.get('name')] = s
    if not allowed:
        return '该系统无可重启的 Docker 服务'
    if not container:
        names = sorted({s.get('container', s.get('name')) for s in allowed.values()})
        return f'请提供容器名，可选: {", ".join(names)}'
    if container not in allowed:
        names = sorted({s.get('container', s.get('name')) for s in allowed.values()})
        return f'不允许重启容器 {container}，可选: {", ".join(names)}'
    target = allowed[container].get('container', container)
    try:
        r = subprocess.run(
            ['docker', 'compose', 'start', target],
            capture_output=True, text=True, cwd=BASE_DIR, timeout=30
        )
        if r.returncode == 0:
            return f'✅ {target} 容器已启动\n{r.stdout.strip()}'
        r2 = subprocess.run(
            ['docker', 'compose', 'up', '-d', target],
            capture_output=True, text=True, cwd=BASE_DIR, timeout=30
        )
        if r2.returncode == 0:
            return f'✅ {target} 容器已通过 up -d 启动\n{r2.stdout.strip()}'
        return f'❌ 启动 {target} 失败:\n{r2.stderr.strip()}'
    except subprocess.TimeoutExpired:
        return f'启动 {target} 超时'
    except Exception as e:
        return f'重启 Docker 容器失败: {e}'


def http_check(params: dict, system: dict) -> str:
    url = params.get('url', '')
    if url:
        try:
            r = requests.get(url, timeout=5)
            size = len(r.content)
            return f'HTTP {r.status_code}  响应大小: {size} bytes  耗时: {r.elapsed.total_seconds():.2f}s'
        except requests.ConnectionError:
            return f'{url} 连接被拒绝 ❌'
        except requests.Timeout:
            return f'{url} 请求超时 ❌'
        except Exception as e:
            return f'请求失败: {e}'

    # 不传 url：检查当前系统所有声明了 HTTP 端点的服务
    endpoints = {}
    for s in system.get('services', []):
        ep = s.get('health_url') or s.get('url')
        if ep:
            endpoints[s.get('name', ep)] = ep
    if not endpoints:
        return '该系统未声明任何 HTTP 端点（服务的 health_url / url）'

    def _check(item):
        name, ep = item
        try:
            r = requests.get(ep, timeout=3)
            return f'{name} ({ep}): HTTP {r.status_code} ✅'
        except requests.ConnectionError:
            return f'{name} ({ep}): 连接拒绝 ❌'
        except Exception as e:
            return f'{name} ({ep}): {e} ❌'

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(endpoints)) as ex:
        results = list(ex.map(_check, endpoints.items()))
    return '\n'.join(results)


# ── OpenAI / DeepSeek Tool 定义（function calling 格式） ─────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_services",
            "description": "列出当前系统所有已注册服务的运行状态（首选入手工具）",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_process",
            "description": "检查当前系统某个已注册服务的健康状态",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "当前系统已注册的服务名（见系统架构）"}
                },
                "required": ["service"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_logs",
            "description": "读取当前系统某个服务的最新日志，帮助分析错误原因",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "当前系统已注册的服务名"},
                    "lines": {"type": "integer", "description": "读取最后 N 行，默认 50"}
                },
                "required": ["service"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_metrics",
            "description": "从当前系统的 Prometheus 查询监控指标，使用 PromQL 语法",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "PromQL 查询，例如: up、node_memory_MemAvailable_bytes、rate(node_cpu_seconds_total[5m])"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "restart_service",
            "description": "重启当前系统某个服务（仅平台托管的本机系统可用；远程接入系统为只读）",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "当前系统已注册的服务名"}
                },
                "required": ["service"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_database",
            "description": "查询当前系统的 MySQL 数据库（只允许 SELECT），获取数据条数、最新记录、异常数据等",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "SELECT SQL 语句"}
                },
                "required": ["sql"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_logs",
            "description": "在当前系统某个服务的日志中搜索关键词，用于定位 ERROR、异常堆栈、特定事件",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "当前系统已注册的服务名"},
                    "keyword": {"type": "string", "description": "搜索关键词，不区分大小写，例如: ERROR、Exception"},
                    "lines": {"type": "integer", "description": "搜索最近 N 行日志，默认 200"}
                },
                "required": ["service", "keyword"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_kafka_status",
            "description": "查询当前系统 Kafka 的 topic 列表、消息详情及消费者 lag（积压量）",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "指定要查询的 topic 名称；不填则列出所有 topic 和消费者 lag"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_redis",
            "description": "执行当前系统 Redis 的只读命令，查询缓存状态、key 数量、内存使用等",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Redis 命令，例如: INFO、DBSIZE、KEYS *、INFO memory"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_info",
            "description": "获取主机系统资源概况：CPU 负载、内存、磁盘、进程数（仅平台托管系统）",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_port",
            "description": "检查指定主机的某个端口是否可达，用于排查网络连通性问题",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "主机名或 IP"},
                    "port": {"type": "integer", "description": "端口号"}
                },
                "required": ["host", "port"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "restart_docker",
            "description": "重启已停止的 Docker 容器（仅平台托管的本机系统可用）",
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {"type": "string", "description": "docker compose 服务名"}
                },
                "required": ["container"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "docker_logs",
            "description": "查看本机 Docker 容器的最新日志（仅平台托管系统；远程系统用 read_logs）",
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {"type": "string", "description": "docker compose 服务名"},
                    "lines": {"type": "integer", "description": "返回最新 N 行，默认 50"}
                },
                "required": ["container"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "http_check",
            "description": "检查 HTTP 端点是否正常响应。不传 url 则自动检查当前系统声明的所有 HTTP 端点",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要检查的 HTTP URL。不填则检查当前系统所有 HTTP 服务"}
                },
                "required": []
            }
        }
    },
]

TOOL_FUNCTIONS = {
    'list_services': list_services,
    'check_process': check_process,
    'read_logs': read_logs,
    'get_metrics': get_metrics,
    'restart_service': restart_service,
    'query_database': query_database,
    'search_logs': search_logs,
    'get_kafka_status': get_kafka_status,
    'query_redis': query_redis,
    'get_system_info': get_system_info,
    'check_port': check_port,
    'docker_logs': docker_logs,
    'restart_docker': restart_docker,
    'http_check': http_check,
}


def execute_tool(name: str, params: dict, system_id: str = 'demo') -> str:
    func = TOOL_FUNCTIONS.get(name)
    if not func:
        return f'未知工具: {name}'
    system = get_system(system_id)
    if system is None:
        return f'未找到系统「{system_id}」，请确认已在 registry/systems/ 注册'
    try:
        return str(func(params or {}, system))
    except Exception as e:
        return f'工具执行出错: {e}'

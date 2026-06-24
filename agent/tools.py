"""
Agent 工具集：12个工具，覆盖进程管理、日志分析、数据库、消息队列、缓存、系统资源
"""
import os
import signal
import socket
import subprocess
import logging
import requests
import mysql.connector
from mysql.connector import Error as MySQLError

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = BASE_DIR
LOGS_DIR = os.path.join(BASE_DIR, 'logs')
PIDS_DIR = os.path.join(BASE_DIR, 'pids')

MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST', 'localhost'),
    'user': os.getenv('MYSQL_USER', 'opsuser'),
    'password': os.getenv('MYSQL_PASSWORD', 'opspass'),
    'database': os.getenv('MYSQL_DB', 'opsdb'),
}
PROMETHEUS_URL = os.getenv('PROMETHEUS_URL', 'http://localhost:9090')

SERVICE_CMDS = {
    'producer': ['python3', os.path.join(BASE_DIR, 'business', 'producer.py')],
    'consumer': ['python3', os.path.join(BASE_DIR, 'business', 'consumer.py')],
    'frontend': ['python3', os.path.join(BASE_DIR, 'business', 'frontend', 'app.py')],
}


# ── 工具实现 ──────────────────────────────────────────────────────────────────

def list_services(_=None) -> str:
    import concurrent.futures

    def _check_processes():
        lines = ['=== Python 业务服务 ===']
        for name in ['producer', 'consumer', 'frontend']:
            running, pid = _is_running(name)
            status = f'运行中 ✅  (PID {pid})' if running else '已停止 ❌'
            lines.append(f'  {name:12s}: {status}')
        return '\n'.join(lines)

    def _check_docker():
        try:
            r = subprocess.run(
                ['docker', 'compose', 'ps', '--format', 'table {{.Name}}\t{{.Status}}'],
                capture_output=True, text=True, cwd=BASE_DIR, timeout=5
            )
            return '\n=== Docker 基础设施 ===\n' + (r.stdout.strip() if r.returncode == 0 else '  (无法获取 Docker 状态)')
        except Exception as e:
            return f'\n=== Docker 基础设施 ===\n  (Docker 查询失败: {e})'

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(_check_processes)
        f2 = ex.submit(_check_docker)
        return f1.result() + f2.result()


def check_process(params: dict) -> str:
    name = params.get('service_name', '')
    running, pid = _is_running(name)
    if running:
        return f'服务 {name} 正在运行，PID: {pid}'
    pid_file = os.path.join(PIDS_DIR, f'{name}.pid')
    if os.path.exists(pid_file):
        return f'服务 {name} 已停止（PID 文件存在但进程不在）'
    return f'服务 {name} 未运行（无 PID 文件）'


def read_logs(params: dict) -> str:
    name = params.get('service_name', '')
    lines = params.get('lines', 50)
    log_file = os.path.join(LOGS_DIR, f'{name}.log')
    if not os.path.exists(log_file):
        return f'日志文件不存在: {log_file}'
    r = subprocess.run(['tail', '-n', str(lines), log_file],
                       capture_output=True, text=True)
    return r.stdout or '日志为空'


def get_metrics(params: dict) -> str:
    query = params.get('query', 'up')
    try:
        r = requests.get(
            f'{PROMETHEUS_URL}/api/v1/query',
            params={'query': query}, timeout=5
        )
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


def restart_service(params: dict) -> str:
    name = params.get('service_name', '')
    if name not in SERVICE_CMDS:
        return f'未知服务: {name}，可选: {list(SERVICE_CMDS.keys())}'

    import time

    # 1. 按 PID 文件 kill
    running, pid = _is_running(name)
    if running and pid:
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info(f'已终止 {name} (PID {pid})')
        except ProcessLookupError:
            pass

    # 2. 兜底：按脚本名 pkill，防止 PID 文件过期导致残留进程占端口
    script = os.path.basename(SERVICE_CMDS[name][-1])
    subprocess.run(['pkill', '-f', script], capture_output=True)
    time.sleep(1)  # 等端口释放

    os.makedirs(LOGS_DIR, exist_ok=True)
    os.makedirs(PIDS_DIR, exist_ok=True)
    log_file = os.path.join(LOGS_DIR, f'{name}.log')
    pid_file = os.path.join(PIDS_DIR, f'{name}.pid')

    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'

    with open(log_file, 'a') as lf:
        proc = subprocess.Popen(SERVICE_CMDS[name], stdout=lf, stderr=lf, env=env)

    with open(pid_file, 'w') as pf:
        pf.write(str(proc.pid))

    time.sleep(2)

    running, new_pid = _is_running(name)
    if running:
        return f'服务 {name} 重启成功 ✅  新 PID: {new_pid}'
    return f'服务 {name} 重启失败 ❌  请查看日志: {log_file}'


def query_database(params: dict) -> str:
    sql = params.get('sql', '').strip()
    if not sql.upper().startswith('SELECT'):
        return '安全限制：只允许 SELECT 查询'
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
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


def search_logs(params: dict) -> str:
    """在日志中搜索关键词，返回匹配行"""
    name = params.get('service_name', '')
    keyword = params.get('keyword', '')
    lines = params.get('lines', 200)
    if not keyword:
        return '请提供要搜索的关键词'
    log_file = os.path.join(LOGS_DIR, f'{name}.log')
    if not os.path.exists(log_file):
        return f'日志文件不存在: {log_file}'
    # 先取最后 N 行，再 grep
    tail = subprocess.run(['tail', '-n', str(lines), log_file],
                          capture_output=True, text=True)
    matches = [l for l in tail.stdout.splitlines() if keyword.lower() in l.lower()]
    if not matches:
        return f"在 {name} 最近 {lines} 行日志中未找到关键词 '{keyword}'"
    return '\n'.join(matches[-50:])  # 最多返回50条匹配


def get_kafka_status(params: dict) -> str:
    """查询 Kafka topic 列表及消费者 lag"""
    topic = params.get('topic', '')
    try:
        # 找 kafka 容器名
        ps = subprocess.run(
            ['docker', 'compose', 'ps', '--format', '{{.Name}}'],
            capture_output=True, text=True, cwd=BASE_DIR, timeout=10
        )
        containers = ps.stdout.strip().splitlines()
        kafka_container = next((c for c in containers if 'kafka' in c.lower()), '')
        if not kafka_container:
            return 'Kafka 容器未运行，请先执行 docker compose up -d'

        if topic:
            # 查某个 topic 的详情
            r = subprocess.run(
                ['docker', 'exec', kafka_container,
                 '/opt/kafka/bin/kafka-topics.sh',
                 '--bootstrap-server', 'localhost:9092',
                 '--describe', '--topic', topic],
                capture_output=True, text=True, timeout=15
            )
            return r.stdout or r.stderr or f'Topic {topic} 不存在'
        else:
            # 列出所有 topic
            r = subprocess.run(
                ['docker', 'exec', kafka_container,
                 '/opt/kafka/bin/kafka-topics.sh',
                 '--bootstrap-server', 'localhost:9092',
                 '--list'],
                capture_output=True, text=True, timeout=15
            )
            topics = r.stdout.strip() or '（无 topic）'

            # 查 sensor-data 的消费者 lag
            lag_r = subprocess.run(
                ['docker', 'exec', kafka_container,
                 '/opt/kafka/bin/kafka-consumer-groups.sh',
                 '--bootstrap-server', 'localhost:9092',
                 '--describe', '--all-groups'],
                capture_output=True, text=True, timeout=15
            )
            lag_output = lag_r.stdout.strip() or '（无消费者组）'
            return f'=== Kafka Topics ===\n{topics}\n\n=== 消费者 Lag ===\n{lag_output}'
    except subprocess.TimeoutExpired:
        return 'Kafka 查询超时'
    except Exception as e:
        return f'Kafka 查询失败: {e}'


def query_redis(params: dict) -> str:
    """执行 Redis 查询命令（只允许只读命令）"""
    command = params.get('command', 'INFO').strip()
    # 安全白名单
    allowed = ('INFO', 'DBSIZE', 'TTL', 'TYPE', 'LLEN', 'SCARD', 'ZCARD', 'HLEN',
               'GET', 'KEYS', 'SCAN', 'SMEMBERS', 'LRANGE', 'HGETALL', 'PING')
    cmd_upper = command.upper().split()[0]
    if cmd_upper not in allowed:
        return f'安全限制：只允许只读命令，不支持 {cmd_upper}'
    try:
        r = subprocess.run(
            ['docker', 'exec', '-i', 'ai-ops-agent-redis-1',
             'redis-cli'] + command.split(),
            capture_output=True, text=True, cwd=BASE_DIR, timeout=10
        )
        if r.returncode != 0 and 'Error' in r.stderr:
            # 尝试备用容器名
            r = subprocess.run(
                ['redis-cli'] + command.split(),
                capture_output=True, text=True, timeout=10
            )
        return r.stdout.strip() or r.stderr.strip() or '（无返回）'
    except FileNotFoundError:
        return 'redis-cli 未找到，请确认 Redis 容器正在运行'
    except Exception as e:
        return f'Redis 查询失败: {e}'


def get_system_info(_=None) -> str:
    """获取系统资源概况：CPU、内存、磁盘、负载"""
    lines = []

    # 系统负载
    try:
        uptime = subprocess.run(['uptime'], capture_output=True, text=True).stdout.strip()
        lines.append(f'负载: {uptime}')
    except Exception:
        pass

    # CPU 使用率（macOS 用 top -l 1）
    try:
        top = subprocess.run(
            ['top', '-l', '1', '-n', '0'],
            capture_output=True, text=True, timeout=10
        )
        for line in top.stdout.splitlines():
            if 'CPU usage' in line or 'cpu' in line.lower():
                lines.append(f'CPU: {line.strip()}')
                break
    except Exception:
        pass

    # 内存（macOS 用 vm_stat）
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

    # 磁盘
    try:
        df = subprocess.run(['df', '-h', '/'], capture_output=True, text=True)
        for line in df.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 5:
                lines.append(f'磁盘(/): 总量 {parts[1]}  已用 {parts[2]} ({parts[4]})')
                break
    except Exception:
        pass

    # 进程总数
    try:
        ps = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        count = len(ps.stdout.strip().splitlines()) - 1
        lines.append(f'进程总数: {count}')
    except Exception:
        pass

    return '\n'.join(lines) if lines else '无法获取系统信息'


def check_port(params: dict) -> str:
    """检查指定主机+端口是否可达"""
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


def docker_logs(params: dict) -> str:
    """查看 Docker 容器的最新日志"""
    container = params.get('container', '')
    lines = params.get('lines', 50)
    if not container:
        return '请提供容器名称（如 kafka、mysql、redis、prometheus）'
    try:
        # 尝试通过 compose 服务名
        r = subprocess.run(
            ['docker', 'compose', 'logs', '--tail', str(lines), '--no-color', container],
            capture_output=True, text=True, cwd=BASE_DIR, timeout=15
        )
        output = r.stdout.strip()
        if not output and r.stderr:
            output = r.stderr.strip()
        return output or f'容器 {container} 无日志输出'
    except subprocess.TimeoutExpired:
        return f'获取 {container} 日志超时'
    except Exception as e:
        return f'获取容器日志失败: {e}'


def restart_docker(params: dict) -> str:
    """重启 Docker Compose 服务（kafka/mysql/redis/prometheus）"""
    container = params.get('container', '')
    ALLOWED = {'kafka', 'mysql', 'redis', 'prometheus'}
    if not container:
        return f'请提供容器名，可选: {", ".join(sorted(ALLOWED))}'
    if container not in ALLOWED:
        return f'不允许重启容器 {container}，可选: {", ".join(sorted(ALLOWED))}'
    try:
        r = subprocess.run(
            ['docker', 'compose', 'start', container],
            capture_output=True, text=True, cwd=BASE_DIR, timeout=30
        )
        if r.returncode == 0:
            return f'✅ {container} 容器已启动\n{r.stdout.strip()}'
        # start 失败时尝试 up -d
        r2 = subprocess.run(
            ['docker', 'compose', 'up', '-d', container],
            capture_output=True, text=True, cwd=BASE_DIR, timeout=30
        )
        if r2.returncode == 0:
            return f'✅ {container} 容器已通过 up -d 启动\n{r2.stdout.strip()}'
        return f'❌ 启动 {container} 失败:\n{r2.stderr.strip()}'
    except subprocess.TimeoutExpired:
        return f'启动 {container} 超时'
    except Exception as e:
        return f'重启 Docker 容器失败: {e}'


def http_check(params: dict) -> str:
    """检查 HTTP/HTTPS 端点是否正常响应"""
    url = params.get('url', '')
    if not url:
        import concurrent.futures
        endpoints = {
            'Frontend': 'http://localhost:5001',
            'Prometheus': 'http://localhost:9090/-/healthy',
            'Webhook': 'http://localhost:8080/webhook',
        }

        def _check(item):
            name, ep = item
            try:
                r = requests.get(ep, timeout=2)
                return f'{name} ({ep}): HTTP {r.status_code} ✅'
            except requests.ConnectionError:
                return f'{name} ({ep}): 连接拒绝 ❌'
            except Exception as e:
                return f'{name} ({ep}): {e} ❌'

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(endpoints)) as ex:
            results = list(ex.map(_check, endpoints.items()))
        return '\n'.join(results)
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


# ── 内部工具函数 ──────────────────────────────────────────────────────────────

def _get_pid(name: str):
    pid_file = os.path.join(PIDS_DIR, f'{name}.pid')
    if not os.path.exists(pid_file):
        return None
    try:
        return int(open(pid_file).read().strip())
    except (ValueError, OSError):
        return None


def _is_running(name: str):
    pid = _get_pid(name)
    if not pid:
        return False, None
    try:
        os.kill(pid, 0)
        return True, pid
    except ProcessLookupError:
        return False, None
    except PermissionError:
        return True, pid


# ── OpenAI / DeepSeek Tool 定义（function calling 格式） ─────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_services",
            "description": "列出所有服务（producer/consumer/frontend）及 Docker 基础设施的运行状态",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_process",
            "description": "检查指定服务的进程是否在运行，返回运行状态和 PID",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "服务名称：producer、consumer 或 frontend"
                    }
                },
                "required": ["service_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_logs",
            "description": "读取指定服务的最新日志内容，帮助分析错误原因",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "服务名称：producer、consumer 或 frontend"
                    },
                    "lines": {
                        "type": "integer",
                        "description": "读取最后 N 行，默认 50"
                    }
                },
                "required": ["service_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_metrics",
            "description": "从 Prometheus 查询监控指标，使用 PromQL 语法",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "PromQL 查询，例如: up、node_memory_MemAvailable_bytes、node_cpu_seconds_total、rate(node_cpu_seconds_total[5m])"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "restart_service",
            "description": "重启指定的服务（先 kill 旧进程，再重新启动）",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "服务名称：producer、consumer 或 frontend"
                    }
                },
                "required": ["service_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_database",
            "description": "查询 MySQL 数据库，获取数据条数、最新记录、异常数据等（只允许 SELECT）",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "SELECT SQL 语句，例如: SELECT COUNT(*) FROM sensor_data; SELECT * FROM sensor_data ORDER BY timestamp DESC LIMIT 10"
                    }
                },
                "required": ["sql"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_logs",
            "description": "在服务日志中搜索关键词，用于定位 ERROR、异常堆栈、特定事件",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "服务名称：producer、consumer、frontend 或 feishu_bot"
                    },
                    "keyword": {
                        "type": "string",
                        "description": "搜索关键词，不区分大小写，例如: ERROR、Exception、Connection refused"
                    },
                    "lines": {
                        "type": "integer",
                        "description": "搜索最近 N 行日志，默认 200"
                    }
                },
                "required": ["service_name", "keyword"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_kafka_status",
            "description": "查询 Kafka 的 topic 列表、消息详情及消费者 lag（积压量）",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "指定要查询的 topic 名称；不填则列出所有 topic 和消费者 lag"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_redis",
            "description": "执行 Redis 只读命令，查询缓存状态、key 数量、内存使用等",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Redis 命令，例如: INFO、DBSIZE、KEYS *、INFO memory、INFO stats"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_info",
            "description": "获取主机系统资源概况：CPU 负载、内存使用、磁盘占用、进程数",
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
                    "host": {
                        "type": "string",
                        "description": "主机名或 IP，例如: localhost、127.0.0.1"
                    },
                    "port": {
                        "type": "integer",
                        "description": "端口号，例如: 9092(Kafka)、3306(MySQL)、6379(Redis)、9090(Prometheus)、5001(Frontend)"
                    }
                },
                "required": ["host", "port"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "restart_docker",
            "description": "重启已停止的 Docker Compose 服务（kafka/mysql/redis/prometheus）。当 Docker 容器意外停止导致基础设施不可用时使用",
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {
                        "type": "string",
                        "description": "docker compose 服务名，可选: kafka、mysql、redis、prometheus"
                    }
                },
                "required": ["container"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "docker_logs",
            "description": "查看 Docker 容器的最新日志，用于排查 Kafka/MySQL/Redis/Prometheus 自身问题",
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {
                        "type": "string",
                        "description": "docker compose 服务名，例如: kafka、mysql、redis、prometheus"
                    },
                    "lines": {
                        "type": "integer",
                        "description": "返回最新 N 行，默认 50"
                    }
                },
                "required": ["container"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "http_check",
            "description": "检查 HTTP 端点是否正常响应。不传 url 则自动检查项目所有服务（Frontend/Prometheus/Webhook）",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要检查的 HTTP URL，例如: http://localhost:5001 。不填则检查所有内置服务"
                    }
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


def execute_tool(name: str, params: dict) -> str:
    func = TOOL_FUNCTIONS.get(name)
    if not func:
        return f'未知工具: {name}'
    try:
        return str(func(params))
    except Exception as e:
        return f'工具执行出错: {e}'

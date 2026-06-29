"""
本机连接器：包住原 tools.py 的本机逻辑（pid 文件探活、tail 本地日志、docker compose）。
仅用于「平台托管」系统（local=true），保证现有 demo 行为零变化。
两种 service kind：
  - process：Python 业务进程，pid 文件探活 + 本地日志文件
  - docker ：docker compose 管理的容器，compose ps/logs 探活与读日志
"""
import os
import signal
import subprocess

from .base import Connector

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _abspath(p: str) -> str:
    if not p:
        return p
    return p if os.path.isabs(p) else os.path.join(_ROOT, p)


def _http_ok(url: str) -> bool:
    try:
        import requests
        r = requests.get(url, timeout=2)
        return r.status_code < 500
    except Exception:
        return False


def _docker_logs(container: str, lines: int) -> str:
    try:
        r = subprocess.run(
            ['docker', 'compose', 'logs', '--tail', str(lines), '--no-color', container],
            capture_output=True, text=True, cwd=_ROOT, timeout=15
        )
        out = r.stdout.strip() or (r.stderr.strip() if r.stderr else '')
        return out or f'容器 {container} 无日志输出'
    except subprocess.TimeoutExpired:
        return f'获取 {container} 日志超时'
    except Exception as e:
        return f'获取容器日志失败: {e}'


class LocalConnector(Connector):
    kind = 'local'

    def _svc_kind(self) -> str:
        return self.service.get('kind', 'process')

    def _log_file(self) -> str:
        return _abspath(self.service.get('log_file') or f'logs/{self.name}.log')

    def _pid_file(self) -> str:
        return _abspath(self.service.get('pid_file') or f'pids/{self.name}.pid')

    def _container(self) -> str:
        return self.service.get('container', self.name)

    def _pid(self):
        pf = self._pid_file()
        if not os.path.exists(pf):
            return None
        try:
            return int(open(pf).read().strip())
        except (ValueError, OSError):
            return None

    def is_running(self):
        pid = self._pid()
        if not pid:
            return False, None
        try:
            os.kill(pid, 0)
            return True, pid
        except ProcessLookupError:
            return False, None
        except PermissionError:
            return True, pid

    # ── 能力实现 ──────────────────────────────────────────────────────────────

    def health(self) -> tuple:
        if self._svc_kind() == 'docker':
            return self._docker_health()
        running, pid = self.is_running()
        if running:
            return True, f'运行中 (PID {pid})'
        url = self.service.get('health_url')
        if url and _http_ok(url):
            return True, f'HTTP 正常 ({url})'
        pf = self._pid_file()
        if os.path.exists(pf):
            return False, '已停止（PID 文件存在但进程不在）'
        return False, '已停止（无 PID 文件）'

    def _docker_health(self) -> tuple:
        container = self._container()
        try:
            r = subprocess.run(
                ['docker', 'compose', 'ps', '--format', '{{.Name}}\t{{.Status}}'],
                capture_output=True, text=True, cwd=_ROOT, timeout=8
            )
            for line in r.stdout.splitlines():
                if container.lower() in line.lower():
                    status = line.split('\t')[-1].strip()
                    ok = any(k in status.lower() for k in ('up', 'running', 'healthy'))
                    return ok, status
            return False, '容器未运行'
        except Exception as e:
            return False, f'查询失败: {e}'

    def read_logs(self, lines: int = 50) -> str:
        if self._svc_kind() == 'docker':
            return _docker_logs(self._container(), lines)
        lf = self._log_file()
        if not os.path.exists(lf):
            return f'日志文件不存在: {lf}'
        r = subprocess.run(['tail', '-n', str(lines), lf], capture_output=True, text=True)
        return r.stdout or '日志为空'

    def search_logs(self, keyword: str, lines: int = 200) -> str:
        if self._svc_kind() == 'docker':
            out = _docker_logs(self._container(), lines)
            matches = [l for l in out.splitlines() if keyword.lower() in l.lower()]
            return '\n'.join(matches[-50:]) or f"在 {self.name} 容器日志中未找到 '{keyword}'"
        lf = self._log_file()
        if not os.path.exists(lf):
            return f'日志文件不存在: {lf}'
        tail = subprocess.run(['tail', '-n', str(lines), lf], capture_output=True, text=True)
        matches = [l for l in tail.stdout.splitlines() if keyword.lower() in l.lower()]
        if not matches:
            return f"在 {self.name} 最近 {lines} 行日志中未找到 '{keyword}'"
        return '\n'.join(matches[-50:])

    # ── 写动作（仅本机进程，供 restart_service 复用）────────────────────────────

    def restart_process(self):
        """kill 旧进程并按 restart_cmd 重启，返回结果字符串。"""
        import time
        cmd = self.service.get('restart_cmd')
        if not cmd:
            return f'服务 {self.name} 未配置 restart_cmd，无法重启'
        cmd = [(_abspath(a) if isinstance(a, str) and a.endswith('.py') else a) for a in cmd]

        running, pid = self.is_running()
        if running and pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        # 兜底 pkill，防止 PID 文件过期残留进程占端口
        script = os.path.basename(cmd[-1])
        subprocess.run(['pkill', '-f', script], capture_output=True)
        time.sleep(1)

        log_file = self._log_file()
        pid_file = self._pid_file()
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        os.makedirs(os.path.dirname(pid_file), exist_ok=True)

        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'
        with open(log_file, 'a') as lf:
            proc = subprocess.Popen(cmd, stdout=lf, stderr=lf, env=env)
        with open(pid_file, 'w') as pf:
            pf.write(str(proc.pid))

        time.sleep(2)
        running, new_pid = self.is_running()
        if running:
            return f'服务 {self.name} 重启成功 ✅  新 PID: {new_pid}'
        return f'服务 {self.name} 重启失败 ❌  请查看日志: {log_file}'

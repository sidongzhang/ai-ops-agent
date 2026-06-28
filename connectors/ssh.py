"""
SSH 连接器：裸机/VM 进程的 agentless 探活与读日志。
通过 subprocess 调系统 ssh（无需 paramiko 依赖），只跑只读命令。
描述符字段：host / user / port / identity_file / log_path /
            health_cmd | systemd_unit | process
"""
import os
import subprocess

from .base import Connector


class SshConnector(Connector):
    kind = 'ssh'

    def _base_cmd(self) -> list:
        cfg = self.service
        cmd = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
               '-o', 'StrictHostKeyChecking=accept-new']
        if cfg.get('port'):
            cmd += ['-p', str(cfg['port'])]
        if cfg.get('identity_file'):
            cmd += ['-i', os.path.expanduser(cfg['identity_file'])]
        host = cfg.get('host', '')
        user = cfg.get('user')
        cmd.append(f'{user}@{host}' if user else host)
        return cmd

    def _run(self, remote_cmd: str, timeout: int = 15):
        if not self.service.get('host'):
            return 1, '未配置 host'
        try:
            r = subprocess.run(
                self._base_cmd() + [remote_cmd],
                capture_output=True, text=True, timeout=timeout
            )
            out = r.stdout or ''
            if r.returncode != 0 and r.stderr:
                out = (out + '\n' + r.stderr).strip()
            return r.returncode, out
        except subprocess.TimeoutExpired:
            return 124, 'SSH 连接/执行超时'
        except Exception as e:
            return 1, f'SSH 执行失败: {e}'

    def health(self) -> tuple:
        cfg = self.service
        if cfg.get('health_cmd'):
            rc, out = self._run(cfg['health_cmd'])
            return (rc == 0), (out.strip()[:200] or ('正常' if rc == 0 else f'退出码 {rc}'))
        if cfg.get('systemd_unit'):
            rc, out = self._run(f"systemctl is-active {cfg['systemd_unit']}")
            state = out.strip()
            return (state == 'active'), (state or f'退出码 {rc}')
        if cfg.get('process'):
            rc, out = self._run(f"pgrep -fl {cfg['process']} | head -5")
            return (rc == 0 and bool(out.strip())), (out.strip() or '进程未找到')
        rc, out = self._run('echo ok')
        return (rc == 0 and 'ok' in out), ('SSH 可达' if rc == 0 else out.strip()[:200])

    def read_logs(self, lines: int = 50) -> str:
        path = self.service.get('log_path')
        if not path:
            return f'服务 {self.name} 未配置 log_path'
        rc, out = self._run(f'tail -n {int(lines)} {path}')
        return out.strip() or '日志为空'

    def search_logs(self, keyword: str, lines: int = 200) -> str:
        path = self.service.get('log_path')
        if not path:
            return f'服务 {self.name} 未配置 log_path'
        kw = keyword.replace("'", '')
        rc, out = self._run(f"tail -n {int(lines)} {path} | grep -i '{kw}' | tail -50")
        return out.strip() or f"未找到 '{keyword}'"

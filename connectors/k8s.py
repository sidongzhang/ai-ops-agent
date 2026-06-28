"""
Kubernetes 连接器（最小实现）：通过本机 kubectl 上下文做 Pod 探活与读日志。
描述符字段：context（可选）/ namespace / selector（如 app=web）。
本里程碑优先级低于 http/tcp/ssh/prometheus，提供基础能力以验证泛化接口。
"""
import subprocess

from .base import Connector


class K8sConnector(Connector):
    kind = 'k8s'

    def _kubectl(self, *args, timeout: int = 15):
        cmd = ['kubectl']
        if self.service.get('context'):
            cmd += ['--context', self.service['context']]
        if self.service.get('namespace'):
            cmd += ['-n', self.service['namespace']]
        cmd += list(args)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return r.returncode, (r.stdout or r.stderr)
        except FileNotFoundError:
            return 127, 'kubectl 未安装'
        except subprocess.TimeoutExpired:
            return 124, 'kubectl 执行超时'
        except Exception as e:
            return 1, str(e)

    def health(self) -> tuple:
        selector = self.service.get('selector')
        if not selector:
            return False, '未配置 selector'
        rc, out = self._kubectl('get', 'pods', '-l', selector, '--no-headers')
        if rc != 0:
            return False, out.strip()[:200]
        rows = [l for l in out.splitlines() if l.strip()]
        if not rows:
            return False, '无匹配 Pod'
        bad = [l for l in rows if 'Running' not in l and 'Completed' not in l]
        summary = '；'.join(f"{l.split()[0]} {l.split()[2]}" for l in rows if len(l.split()) >= 3)
        return (not bad), summary[:300]

    def read_logs(self, lines: int = 50) -> str:
        selector = self.service.get('selector')
        if not selector:
            return '未配置 selector'
        rc, out = self._kubectl('logs', '-l', selector, '--tail', str(lines))
        return out.strip() or '日志为空'

    def search_logs(self, keyword: str, lines: int = 200) -> str:
        out = self.read_logs(lines)
        matches = [l for l in out.splitlines() if keyword.lower() in l.lower()]
        return '\n'.join(matches[-50:]) or f"未找到 '{keyword}'"

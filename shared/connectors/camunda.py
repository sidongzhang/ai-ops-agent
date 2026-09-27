"""Camunda（内嵌于 algp-web 的流程引擎）连接器。

背景：ALGP 的 Camunda 以 spring-boot-starter 形式内嵌在 algp-web 主进程里，
没有独立的 8101 engine-rest 服务；8101 只是配置里的外部引擎地址。
健康判断依据 algp-web 自身 actuator/health 的细节组件：
  - components.processEngine / jobExecutor（Camunda 内嵌引擎与 Job 执行器）存在且 UP
  - actuator/prometheus 里的 rabbitmq_* 连接指标作为佐证（外部任务客户端共用连接池）
"""
import requests

from .base import Connector


class CamundaEmbeddedConnector(Connector):
    kind = 'camunda_embedded'

    def _health_url(self) -> str:
        config = self.service.get('config') or {}
        explicit = str(config.get('health_url') or '').strip()
        if explicit:
            return explicit
        # 默认：从同系统 spring-boot 服务的 health_url（actuator/health，show-details=always）
        for svc in self.system.get('services', []):
            health_url = str(svc.get('health_url') or '')
            if 'actuator/health' in health_url:
                return health_url
        return ''

    def _scrape(self, url: str):
        try:
            r = requests.get(url, timeout=6)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    def health(self) -> tuple:
        url = self._health_url()
        if not url:
            return False, '未配置宿主应用 actuator/health 地址（config.health_url）'
        payload = self._scrape(url)
        if not payload:
            return False, f'actuator/health 不可达：{url}'
        components = payload.get('components') or payload.get('details') or {}
        if not components:
            return False, f'actuator/health 无组件详情（需 management.endpoint.health.show-details=always）：{url}'
        camunda_keys = {k: v for k, v in components.items()
                        if k.lower() in ('processengine', 'jobexecutor') or 'camunda' in k.lower()}
        if camunda_keys:
            states = [str(v.get('status', '?')) if isinstance(v, dict) else '?' for v in camunda_keys.values()]
            ok = all(s == 'UP' for s in states)
            names = ', '.join(f'{k}={s}' for k, s in zip(camunda_keys.keys(), states))
            return (ok, f"Camunda 内嵌引擎：{names}")
        return False, f'algp-web 健康组件里没有 processEngine/jobExecutor，流程引擎可能未加载（{url}）'

    def read_logs(self, lines: int = 50) -> str:
        return f'服务 {self.name} 为内嵌流程引擎，请查看宿主应用（algp-web）日志'

    def search_logs(self, keyword: str, lines: int = 200) -> str:
        return f'服务 {self.name} 为内嵌流程引擎，请检索宿主应用（algp-web）日志中的 {keyword}'

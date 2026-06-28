"""
Prometheus 连接器：拉对方 Prometheus 的指标做探活（agentless）。
service.url 优先；否则回退 system.infra.prometheus.url。
service.up_query 指定探活用的 PromQL（默认 up），结果中任一序列值 >=1 视为正常。
"""
import requests

from .base import Connector


class PrometheusConnector(Connector):
    kind = 'prometheus'

    def _url(self):
        return (self.service.get('url')
                or ((self.system.get('infra', {}) or {}).get('prometheus', {}) or {}).get('url'))

    def health(self) -> tuple:
        url = self._url()
        if not url:
            return False, '未配置 Prometheus url'
        query = self.service.get('up_query') or 'up'
        try:
            r = requests.get(f'{url}/api/v1/query', params={'query': query}, timeout=5)
            if r.status_code != 200:
                return False, f'Prometheus HTTP {r.status_code}'
            result = r.json().get('data', {}).get('result', [])
            if not result:
                return False, f"查询 '{query}' 无结果"
            vals = []
            for item in result:
                try:
                    vals.append(float(item.get('value', [0, '0'])[1]))
                except (ValueError, TypeError):
                    pass
            ok = any(v >= 1 for v in vals)
            return ok, f"{query} => {vals[:5]}"
        except Exception as e:
            return False, str(e)

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
            payload = r.json()
            if payload.get('status') != 'success':
                return False, f"PromQL 无效：{payload.get('error', '查询失败')}"
            result = payload.get('data', {}).get('result', [])
            if not result:
                return False, f"指标校验失败：查询 '{query}' 无结果，请确认指标名和抓取目标"
            vals = []
            for item in result:
                try:
                    vals.append(float(item.get('value', [0, '0'])[1]))
                except (ValueError, TypeError):
                    pass
            if not vals:
                return False, f"指标校验失败：查询 '{query}' 未返回数值"
            ok = any(v >= 1 for v in vals)
            if not ok:
                return False, f"指标存在但当前值均小于 1：{query} => {vals[:5]}"
            return True, f"指标校验通过：{query} => {vals[:5]}"
        except Exception as e:
            return False, str(e)

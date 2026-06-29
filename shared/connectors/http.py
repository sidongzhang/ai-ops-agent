"""
HTTP 连接器：对外网站/接口的黑盒探测（agentless）。只读，无日志能力。
"""
import requests

from .base import Connector


class HttpConnector(Connector):
    kind = 'http'

    def _url(self):
        return self.service.get('health_url') or self.service.get('url')

    def health(self) -> tuple:
        url = self._url()
        if not url:
            return False, '未配置 health_url'
        try:
            r = requests.get(url, timeout=5)
            ok = r.status_code < 500
            detail = f'HTTP {r.status_code}  {r.elapsed.total_seconds():.2f}s'
            return ok, detail
        except requests.ConnectionError:
            return False, '连接被拒绝'
        except requests.Timeout:
            return False, '请求超时'
        except Exception as e:
            return False, str(e)

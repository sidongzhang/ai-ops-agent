"""
TCP 连接器：端口连通性探测（agentless）。只读，无日志能力。
"""
import socket

from .base import Connector


class TcpConnector(Connector):
    kind = 'tcp'

    def health(self) -> tuple:
        host = self.service.get('host', 'localhost')
        port = int(self.service.get('port', 0) or 0)
        if not port:
            return False, '未配置 port'
        timeout = float(self.service.get('timeout', 3))
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            rc = sock.connect_ex((host, port))
            sock.close()
            if rc == 0:
                return True, f'{host}:{port} 可达'
            return False, f'{host}:{port} 不可达 (rc={rc})'
        except socket.gaierror as e:
            return False, f'域名解析失败 {host}: {e}'
        except Exception as e:
            return False, str(e)

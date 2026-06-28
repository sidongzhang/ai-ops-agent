"""
连接器包：按服务描述符里的 connector 字段实例化对应连接器。
内置 local / http / tcp / ssh / prometheus / k8s 六种，新增类型只需在此注册。
"""
from .base import Connector
from .local import LocalConnector
from .http import HttpConnector
from .tcp import TcpConnector
from .ssh import SshConnector
from .prometheus import PrometheusConnector
from .k8s import K8sConnector

_REGISTRY = {
    'local': LocalConnector,
    'http': HttpConnector,
    'tcp': TcpConnector,
    'ssh': SshConnector,
    'prometheus': PrometheusConnector,
    'k8s': K8sConnector,
}


def get_connector(service: dict, system: dict) -> Connector:
    kind = (service or {}).get('connector', 'local')
    cls = _REGISTRY.get(kind, LocalConnector)
    return cls(service, system)


__all__ = ['Connector', 'get_connector']

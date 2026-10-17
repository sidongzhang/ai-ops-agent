"""
连接器包：按服务描述符里的 connector 字段实例化对应连接器。
内置 local / http / tcp / ssh / prometheus / k8s / camunda_embedded 七种，
新增类型只需在此注册。
"""
from .base import Connector
from .local import LocalConnector
from .http import HttpConnector
from .tcp import TcpConnector
from .ssh import SshConnector
from .prometheus import PrometheusConnector
from .k8s import K8sConnector
from .camunda import CamundaEmbeddedConnector

_REGISTRY = {
    'local': LocalConnector,
    'http': HttpConnector,
    'tcp': TcpConnector,
    'ssh': SshConnector,
    'prometheus': PrometheusConnector,
    'k8s': K8sConnector,
    'camunda_embedded': CamundaEmbeddedConnector,
}


def get_connector(service: dict, system: dict) -> Connector:
    kind = (service or {}).get('connector', 'local') or 'local'
    cls = _REGISTRY.get(kind)
    if cls is None:
        # 以前未知类型会静默回退 LocalConnector：拼错一个字母就会拿 127.0.0.1
        # 去探活，把"本机端口不通"写进诊断结论。这里改为显式失败。
        raise ValueError(
            f"未知的连接器类型 {kind!r}；可用类型：{', '.join(sorted(_REGISTRY))}"
        )
    return cls(service, system)


__all__ = ['Connector', 'get_connector']

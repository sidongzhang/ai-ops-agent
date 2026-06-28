"""
服务注册表包：把「被监控系统」从源码硬编码外置为可注册的 YAML 描述符。
每个系统一份 registry/systems/<id>.yaml，描述其服务、连接方式与基础设施连接信息。
"""
from .registry import (
    list_systems,
    list_system_ids,
    get_system,
    list_services,
    get_service,
    resolve_system_by_chat,
    add_system,
    project_root,
)

__all__ = [
    'list_systems',
    'list_system_ids',
    'get_system',
    'list_services',
    'get_service',
    'resolve_system_by_chat',
    'add_system',
    'project_root',
]

"""Descriptor utilities split by responsibility."""
from .builder import ServiceDescriptor, SystemDescriptor, service_to_descriptor, system_to_descriptor
from .health import collect_health, read_service_logs, search_service_logs
from .prompt import build_prompt

__all__ = [
    "ServiceDescriptor",
    "SystemDescriptor",
    "service_to_descriptor",
    "system_to_descriptor",
    "collect_health",
    "read_service_logs",
    "search_service_logs",
    "build_prompt",
]

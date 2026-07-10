"""Descriptor construction from persisted models."""
from typing import Any, TypedDict

from ...core.security import decrypt_sensitive_fields
from ...models.systems import MonitoredSystem, Service


class ServiceDescriptor(TypedDict, total=False):
    name: str
    connector: str
    config: dict[str, Any]
    runtime: dict[str, str]
    kind: str
    health_url: str
    url: str
    host: str
    port: int
    log_path: str
    log_file: str
    container: str
    selector: str
    systemd_unit: str
    up_query: str


class SystemDescriptor(TypedDict):
    id: str
    name: str
    local: bool
    infra: dict[str, Any]
    services: list[ServiceDescriptor]


def service_to_descriptor(service: Service) -> ServiceDescriptor:
    decrypted_config = decrypt_sensitive_fields(service.config or {})
    descriptor: ServiceDescriptor = {
        "name": service.name,
        "connector": service.connector,
        "config": decrypted_config,
        "runtime": {
            "container": str(decrypted_config.get("container", "") or ""),
            "systemd_unit": str(decrypted_config.get("systemd_unit", "") or ""),
            "selector": str(decrypted_config.get("selector", "") or ""),
        },
    }
    descriptor.update(decrypted_config)
    return descriptor


def system_to_descriptor(system: MonitoredSystem, services: list[Service]) -> SystemDescriptor:
    return {
        "id": f"org{system.org_id}-{system.key}",
        "name": system.name,
        "local": system.local,
        "infra": decrypt_sensitive_fields(system.infra or {}),
        "services": [service_to_descriptor(service) for service in services],
    }

"""Health and log operations over descriptors."""
import concurrent.futures

from app.core import metrics

from .builder import SystemDescriptor
from .runtime import get_connector


def _connector(service: dict, descriptor: SystemDescriptor):
    """未知 connector 是配置错误：计数后继续抛，让调用方给出明确失败原因。"""
    try:
        return get_connector(service, descriptor)
    except ValueError:
        metrics.inc("aiops_unknown_connector_total", kind=str(service.get("connector", "")))
        raise


def collect_health(descriptor: SystemDescriptor) -> list[dict]:
    services = descriptor.get("services", [])
    if not services:
        return []

    def check_one(service: dict) -> dict:
        try:
            ok, detail = _connector(service, descriptor).health()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"检查失败: {exc}"
        return {
            "name": service.get("name", ""),
            "ok": ok,
            "detail": detail,
            "connector": service.get("connector", ""),
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(services))) as executor:
        return list(executor.map(check_one, services))


def read_service_logs(descriptor: SystemDescriptor, service_name: str, lines: int = 50) -> str:
    service = next((item for item in descriptor.get("services", []) if item.get("name") == service_name), None)
    if not service:
        return f"系统中无服务「{service_name}」"
    return _connector(service, descriptor).read_logs(lines)


def search_service_logs(
    descriptor: SystemDescriptor,
    service_name: str,
    keyword: str,
    lines: int = 200,
) -> str:
    service = next((item for item in descriptor.get("services", []) if item.get("name") == service_name), None)
    if not service:
        return f"系统中无服务「{service_name}」"
    return _connector(service, descriptor).search_logs(keyword, lines)

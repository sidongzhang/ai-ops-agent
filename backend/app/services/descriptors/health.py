"""Health and log operations over descriptors."""
import concurrent.futures

from .builder import SystemDescriptor
from .runtime import get_connector


def collect_health(descriptor: SystemDescriptor) -> list[dict]:
    services = descriptor.get("services", [])
    if not services:
        return []

    def check_one(service: dict) -> dict:
        try:
            ok, detail = get_connector(service, descriptor).health()
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
    return get_connector(service, descriptor).read_logs(lines)


def search_service_logs(
    descriptor: SystemDescriptor,
    service_name: str,
    keyword: str,
    lines: int = 200,
) -> str:
    service = next((item for item in descriptor.get("services", []) if item.get("name") == service_name), None)
    if not service:
        return f"系统中无服务「{service_name}」"
    return get_connector(service, descriptor).search_logs(keyword, lines)

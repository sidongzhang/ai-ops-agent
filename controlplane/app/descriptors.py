"""
桥接层：把 DB 里的 MonitoredSystem/Service 行还原成「连接器描述符」字典，
从而 **原样复用仓库根目录的 connectors/ 包**（local/http/tcp/ssh/prometheus/k8s）。
这层是「业务逻辑不重写、只换骨架」的具体落点。
"""
import os
import sys
import concurrent.futures

from .config import settings
from .models import MonitoredSystem, Service

# 让控制面能 import 顶层 connectors 包
if settings.repo_root not in sys.path:
    sys.path.insert(0, settings.repo_root)

from connectors import get_connector  # noqa: E402


def service_to_descriptor(s: Service) -> dict:
    d = {"name": s.name, "connector": s.connector}
    d.update(s.config or {})
    return d


def system_to_descriptor(system: MonitoredSystem, services: list[Service]) -> dict:
    return {
        "id": f"org{system.org_id}-{system.key}",
        "name": system.name,
        "local": system.local,
        "infra": system.infra or {},
        "services": [service_to_descriptor(s) for s in services],
    }


def collect_health(descriptor: dict) -> list[dict]:
    """并发探活，返回 [{name, ok, detail, connector}]。复用连接器，不碰被监控系统写动作。"""
    services = descriptor.get("services", [])
    if not services:
        return []

    def _one(svc: dict) -> dict:
        try:
            ok, detail = get_connector(svc, descriptor).health()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"检查失败: {e}"
        return {"name": svc.get("name", ""), "ok": ok, "detail": detail,
                "connector": svc.get("connector", "")}

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(services))) as ex:
        return list(ex.map(_one, services))


def read_service_logs(descriptor: dict, service_name: str, lines: int = 50) -> str:
    svc = next((s for s in descriptor.get("services", []) if s.get("name") == service_name), None)
    if not svc:
        return f"系统中无服务「{service_name}」"
    return get_connector(svc, descriptor).read_logs(lines)


def search_service_logs(descriptor: dict, service_name: str, keyword: str, lines: int = 200) -> str:
    svc = next((s for s in descriptor.get("services", []) if s.get("name") == service_name), None)
    if not svc:
        return f"系统中无服务「{service_name}」"
    return get_connector(svc, descriptor).search_logs(keyword, lines)


def build_prompt(descriptor: dict) -> str:
    """从系统描述符动态生成运维 agent 的系统提示（拓扑来自注册数据，而非硬编码）。"""
    name = descriptor.get("name") or descriptor.get("id", "")
    lines = [
        f"你是一个智能运维助手（AI Ops Agent），负责监控和诊断「{name}」这套系统。",
        "",
        "## 系统架构（已注册的服务）",
    ]
    for s in descriptor.get("services", []):
        parts = [f"connector={s.get('connector', 'http')}"]
        for k in ("kind", "health_url", "url", "host", "port", "log_path",
                  "log_file", "container", "selector", "systemd_unit", "up_query"):
            if s.get(k):
                parts.append(f"{k}={s[k]}")
        lines.append(f"- **{s.get('name', '')}**：{', '.join(parts)}")

    mode = ("平台托管（可执行写动作）" if descriptor.get("local")
            else "agentless 远程接入（只读监控，不可远程修复，需给人工建议）")
    lines += [
        "",
        f"## 接入模式\n本系统为 {mode}。",
        "",
        "## 可用工具",
        "- list_services：列出所有服务的健康状态（首选入手）",
        "- check_service：检查单个服务的健康",
        "- read_logs / search_logs：读取/搜索某服务日志",
        "",
        "## 工作原则",
        "1. 先 list_services 看全局；2. 对异常服务 check_service 确认 + read_logs/search_logs 定位；",
        "3. 输出【问题报告】：发现的问题 → 根因 → 处置建议 → 当前状态。",
        "用中文，专业简洁。",
    ]
    return "\n".join(lines)

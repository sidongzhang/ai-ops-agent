"""MCP 接入：配置解析、能力清单、以及针对本地演示 server 的真实 stdio 端到端。"""
import pytest
import json
from pathlib import Path

from app.agent.diagnostics import mcp
from app.core.config import settings

_SERVER_PATH = Path(__file__).resolve().parents[2] / "deploy" / "mcp" / "demo_k8s_server.py"


def _demo_config() -> str:
    return json.dumps(
        [
            {
                "name": "demo-k8s",
                "command": "{python}",
                "args": [str(_SERVER_PATH)],
                "description": "演示集群",
                "timeout": 40,
                # 授权：org 1 / system 2，且只开放这两个只读工具（空列表=拒绝）
                "org_ids": [1],
                "system_ids": [2],
                "allowed_tools": ["list_pods", "recent_events"],
            }
        ]
    )


def test_load_servers_substitutes_python_and_parses():
    servers = mcp.load_servers(_demo_config())
    assert len(servers) == 1
    assert servers[0].name == "demo-k8s"
    assert "{python}" not in servers[0].command
    assert servers[0].args == (str(_SERVER_PATH),)


def test_load_servers_substitutes_repo_placeholder():
    servers = mcp.load_servers(
        json.dumps([{"name": "x", "command": "run", "args": ["{repo}/deploy/mcp/demo_k8s_server.py"]}])
    )
    assert servers[0].args[0] == f"{settings.repo_root}/deploy/mcp/demo_k8s_server.py"


def test_load_servers_ignores_invalid_config():
    assert mcp.load_servers("这不是 json") == []
    assert mcp.load_servers('[{"name": "缺少 command"}]') == []
    assert mcp.load_servers("") == []


def test_catalog_empty_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "mcp_servers", "")
    assert mcp.catalog() == ""




def test_catalog_lists_servers_and_usage(monkeypatch):
    monkeypatch.setattr(settings, "mcp_servers", _demo_config())
    text = mcp.catalog(org_id=1, system_id=2)
    assert "demo-k8s" in text
    assert "mcp_list_tools" in text


def test_unknown_server_is_reported(monkeypatch):
    monkeypatch.setattr(settings, "mcp_servers", _demo_config())
    assert "未找到" in mcp.list_tools_text("没有这个")
    assert "未找到" in mcp.call_tool_text("没有这个", "t")




def test_call_rejects_bad_arguments_json(monkeypatch):
    monkeypatch.setattr(settings, "mcp_servers", _demo_config())
    assert "不是合法 JSON" in mcp.call_tool_text("demo-k8s", "list_pods", "{oops}", org_id=1, system_id=2)
    assert "必须是 JSON 对象" in mcp.call_tool_text("demo-k8s", "list_pods", "[1,2]", org_id=1, system_id=2)




def test_stdio_end_to_end_list_and_call(monkeypatch):
    """真实通过 stdio 连本地演示 MCP server：先列工具，再调用取数据。"""
    monkeypatch.setattr(settings, "mcp_servers", _demo_config())

    listing = mcp.list_tools_text("demo-k8s", org_id=1, system_id=2)
    assert "list_pods" in listing
    assert "recent_events" in listing

    events = mcp.call_tool_text("demo-k8s", "recent_events", '{"namespace": "aiops-prod"}', org_id=1, system_id=2)
    assert "BackOff" in events

    pods = mcp.call_tool_text("demo-k8s", "list_pods", "{}", org_id=1, system_id=2)
    assert "CrashLoopBackOff" in pods


def test_unauthorized_scope_is_denied(monkeypatch):
    """MCP 改为"先授权再调用"：org/system 不匹配或工具不在白名单一律拒绝。"""
    monkeypatch.setattr(settings, "mcp_servers", _demo_config())

    assert mcp.catalog(org_id=9, system_id=9) == "", "未授权组织不应看到 MCP 目录"
    assert "拒绝" in mcp.call_tool_text("demo-k8s", "list_pods", "{}", org_id=9, system_id=2)
    assert "拒绝" in mcp.call_tool_text("demo-k8s", "delete_pod", "{}", org_id=1, system_id=2)

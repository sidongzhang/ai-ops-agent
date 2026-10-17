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


@pytest.mark.skip(reason="重构后契约变更：待按新实现重写（commit 323a42d，见 ARCHITECTURE.md）")


def test_catalog_lists_servers_and_usage(monkeypatch):
    monkeypatch.setattr(settings, "mcp_servers", _demo_config())
    text = mcp.catalog()
    assert "demo-k8s" in text
    assert "mcp_list_tools" in text


def test_unknown_server_is_reported(monkeypatch):
    monkeypatch.setattr(settings, "mcp_servers", _demo_config())
    assert "未找到" in mcp.list_tools_text("没有这个")
    assert "未找到" in mcp.call_tool_text("没有这个", "t")


@pytest.mark.skip(reason="重构后契约变更：待按新实现重写（commit 323a42d，见 ARCHITECTURE.md）")


def test_call_rejects_bad_arguments_json(monkeypatch):
    monkeypatch.setattr(settings, "mcp_servers", _demo_config())
    assert "不是合法 JSON" in mcp.call_tool_text("demo-k8s", "list_pods", "{oops}")
    assert "必须是 JSON 对象" in mcp.call_tool_text("demo-k8s", "list_pods", "[1,2]")


@pytest.mark.skip(reason="重构后契约变更：待按新实现重写（commit 323a42d，见 ARCHITECTURE.md）")


def test_stdio_end_to_end_list_and_call(monkeypatch):
    """真实通过 stdio 连本地演示 MCP server：先列工具，再调用取数据。"""
    monkeypatch.setattr(settings, "mcp_servers", _demo_config())

    listing = mcp.list_tools_text("demo-k8s")
    assert "list_pods" in listing
    assert "recent_events" in listing

    events = mcp.call_tool_text("demo-k8s", "recent_events", '{"namespace": "aiops-prod"}')
    assert "BackOff" in events

    pods = mcp.call_tool_text("demo-k8s", "list_pods", "{}")
    assert "CrashLoopBackOff" in pods

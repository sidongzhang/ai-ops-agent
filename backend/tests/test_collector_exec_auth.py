"""collector/exec 的权限边界：viewer 不能向客户内网下发命令。

这个接口可以执行只读 SQL / Redis / Kafka 命令；此前只要求"登录"，
任何查看者账号都能主动取证。现在要求 owner/operator。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import collector_exec
from app.core.deps import get_current_org_id, get_current_user


class _User:
    def __init__(self, role: str):
        self.id = 9
        self.org_id = 1
        self.role = role


def _client(role: str) -> TestClient:
    app = FastAPI()
    app.include_router(collector_exec.router)
    app.dependency_overrides[get_current_user] = lambda: _User(role)
    app.dependency_overrides[get_current_org_id] = lambda: 1
    return TestClient(app)


def test_viewer_is_forbidden() -> None:
    response = _client("viewer").post(
        "/systems/1/collector/exec", json={"cmd": "health_check", "args": {}}
    )
    assert response.status_code == 403
    assert "查看权限" in response.json()["detail"]


def test_restart_still_requires_approval_for_operator() -> None:
    response = _client("operator").post(
        "/systems/1/collector/exec", json={"cmd": "restart_container", "args": {}}
    )
    assert response.status_code == 403
    assert "审批" in response.json()["detail"]

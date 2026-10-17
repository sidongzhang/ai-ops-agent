"""沙箱降级护栏：宁可工具失败，也不在 API 进程内执行模型代码。

历史上 `_docker_argv()` 在「配置了镜像但 docker 不可用」时返回 None，
`run_python()` 于是静默改用本地解释器执行——那个进程读得到 `.env`、
连得上内网与数据库。
"""
import pytest

from app.core.config import settings
from app.services.diagnostics import sandbox


def test_blocks_when_image_configured_but_docker_missing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sandbox_container_image", "aiops-sandbox:latest")
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: None)

    result = sandbox.run_python("print('should not run')")

    assert result["ok"] is False
    assert "沙箱不可用" in result["stderr"]
    assert "docker" in result["stderr"]
    assert result["outputs"] == {}
    assert result["stdout"] == ""


def test_production_without_image_is_blocked(monkeypatch) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "sandbox_container_image", "")

    assert sandbox.blocked_reason() is not None
    assert "SANDBOX_CONTAINER_IMAGE" in sandbox.blocked_reason()


def test_dev_without_image_still_runs_inprocess(monkeypatch) -> None:
    monkeypatch.setattr(settings, "environment", "dev")
    monkeypatch.setattr(settings, "sandbox_container_image", "")

    assert sandbox.blocked_reason() is None

    result = sandbox.run_python("print(1 + 1)")
    assert result["ok"] is True
    assert "2" in result["stdout"]


def test_dev_with_image_and_docker_present_is_allowed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "environment", "dev")
    monkeypatch.setattr(settings, "sandbox_container_image", "aiops-sandbox:latest")
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: "/usr/local/bin/docker")

    assert sandbox.blocked_reason() is None

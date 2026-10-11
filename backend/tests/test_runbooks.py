"""规程（runbook）：目录解析、按名加载、frontmatter 剥离、缺失处理。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agent.diagnostics.knowledge import runbooks as rb


def _make(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(rb, "_docs_dir", lambda sid: tmp_path / str(sid))
    directory = tmp_path / "7" / "runbooks"
    directory.mkdir(parents=True)
    return directory


def test_catalog_lists_name_and_description(tmp_path, monkeypatch):
    directory = _make(tmp_path, monkeypatch)
    (directory / "a.md").write_text(
        "---\nname: 归档解读\ndescription: 看归档文件时用\n---\n正文步骤\n", encoding="utf-8"
    )
    catalog = rb.describe_catalog(7)
    assert "归档解读" in catalog
    assert "看归档文件时用" in catalog
    assert "load_runbook" in catalog


def test_load_returns_body_without_frontmatter(tmp_path, monkeypatch):
    directory = _make(tmp_path, monkeypatch)
    (directory / "a.md").write_text(
        "---\nname: 归档解读\ndescription: x\n---\n步骤1\n步骤2\n", encoding="utf-8"
    )
    body = rb.load_runbook(7, "归档解读")
    assert body is not None
    assert body.startswith("步骤1")
    assert "description" not in body


def test_missing_returns_none_and_empty_catalog(tmp_path, monkeypatch):
    _make(tmp_path, monkeypatch)
    assert rb.load_runbook(7, "不存在") is None
    assert rb.describe_catalog(7) == ""

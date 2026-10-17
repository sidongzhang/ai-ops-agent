"""附件下载 token 的边界：TTL、system 绑定、无元数据 fail-closed。"""
import json
import time

import pytest

from app.core.config import settings
from app.services.diagnostics.artifacts import find_artifact, save_artifact


@pytest.fixture
def artifact_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "diagnosis_artifact_dir", str(tmp_path))
    monkeypatch.setattr(settings, "artifact_ttl_hours", 168)
    return tmp_path


def test_saved_artifact_is_downloadable_within_ttl(artifact_dir) -> None:
    token = save_artifact("report.png", b"PNG", org_id=1, system_id=3)

    found = find_artifact(token, system_id=3)
    assert found is not None
    path, name = found
    assert path.read_bytes() == b"PNG"
    assert name == "report.png"


def test_token_is_bound_to_system(artifact_dir) -> None:
    token = save_artifact("report.png", b"PNG", org_id=1, system_id=3)

    assert find_artifact(token, system_id=4) is None
    assert find_artifact(token, system_id=3) is not None


def test_expired_artifact_is_rejected(artifact_dir, monkeypatch) -> None:
    token = save_artifact("report.png", b"PNG", org_id=1, system_id=3)
    meta_path = artifact_dir / f"{token}.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["created_at"] = time.time() - 10
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(settings, "artifact_ttl_hours", 0.001)  # 3.6 秒

    assert find_artifact(token, system_id=3) is None


def test_legacy_artifact_without_metadata_is_rejected(artifact_dir) -> None:
    token = "a" * 32
    (artifact_dir / f"{token}__legacy.txt").write_bytes(b"old")

    assert find_artifact(token, system_id=3) is None


def test_malformed_token_is_rejected(artifact_dir) -> None:
    assert find_artifact("../../etc/passwd", system_id=3) is None
    assert find_artifact("abc", system_id=3) is None


def test_save_purges_expired_artifacts(artifact_dir, monkeypatch) -> None:
    old = save_artifact("old.png", b"x", org_id=1, system_id=3)
    meta_path = artifact_dir / f"{old}.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["created_at"] = time.time() - 100
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(settings, "artifact_ttl_hours", 0.001)

    save_artifact("new.png", b"y", org_id=1, system_id=3)

    assert not (artifact_dir / f"{old}.meta.json").exists()
    assert not list(artifact_dir.glob(f"{old}__*"))

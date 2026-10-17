"""诊断附件落盘：保存/按 token 查找，以及非法 token 拒绝。"""
from app.core.config import settings
from app.services.diagnostics import artifacts as art


def test_save_and_find(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "diagnosis_artifact_dir", str(tmp_path))
    token = art.save_artifact("a_loc.fits", b"hello-bytes")
    assert len(token) == 32
    found = art.find_artifact(token)
    assert found is not None
    path, name = found
    assert name == "a_loc.fits"
    assert path.read_bytes() == b"hello-bytes"


def test_find_rejects_bad_token(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "diagnosis_artifact_dir", str(tmp_path))
    assert art.find_artifact("../etc/passwd") is None
    assert art.find_artifact("deadbeef") is None
    assert art.find_artifact("") is None

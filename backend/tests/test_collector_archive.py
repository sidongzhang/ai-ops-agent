"""归档文件只读访问：路径安全与类型识别（采集器侧纯函数回归）。

安全要点：归档目录必须落在白名单根目录内，且拒绝任何路径穿越。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 仓库根，便于 import collector

from collector import ws_client as w  # noqa: E402


def test_archive_dir_rejects_traversal():
    assert w._archive_dir("/tmp", "../../etc") is None
    assert w._archive_dir("/tmp", "..") is None
    assert w._archive_dir("/tmp", "/etc/passwd") is None
    assert w._archive_dir("/tmp", "") is None


def test_archive_dir_computes_year_month(tmp_path):
    directory = w._archive_dir(str(tmp_path), "tn260717_120003_gbm")
    assert directory is not None
    assert directory.endswith("2026/07/tn260717_120003_gbm")


def test_archive_kind_classification():
    assert w._archive_kind("result_x_loc.fits") == "loc"
    assert w._archive_kind("result_x_joint.fits") == "joint"
    assert w._archive_kind("result_x.pdf") == "pdf"
    assert w._archive_kind("GRD_evt.tcat") == "tcat"
    assert w._archive_kind("location_x.json") == "json"
    assert w._archive_kind("unknown.bin") == "other"


def test_archive_read_blocks_escape(tmp_path):
    # 目录内没有该文件（且 file_name 会被 basename 归一），应拒绝
    resp = w._archive_read({"base_path": str(tmp_path), "tcat_id": "tn260717_120003_gbm", "file_name": "../../../etc/passwd"})
    assert resp["ok"] is False

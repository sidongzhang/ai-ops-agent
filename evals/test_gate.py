"""评测门禁门禁（evals/gate.py）自测：阈值判定与格式。"""
import json

import gate


def _summary(metrics: dict) -> dict:
    return {
        "meta": {"primary_group": "full"},
        "overall": metrics,
        "ablations": {"full": {"overall": metrics}},
    }


def test_min_and_max_pass():
    summary = _summary({"task_success_rate": 90, "hallucination_rate": 5})
    rows = gate.evaluate(summary, {"min": {"task_success_rate": 80}, "max": {"hallucination_rate": 10}})
    assert gate.passed(rows) is True


def test_min_fails_when_below():
    summary = _summary({"task_success_rate": 50})
    rows = gate.evaluate(summary, {"min": {"task_success_rate": 80}})
    assert gate.passed(rows) is False


def test_max_fails_when_above():
    summary = _summary({"hallucination_rate": 30})
    rows = gate.evaluate(summary, {"max": {"hallucination_rate": 10}})
    assert gate.passed(rows) is False


def test_missing_metric_fails():
    rows = gate.evaluate(_summary({}), {"min": {"task_success_rate": 1}})
    assert gate.passed(rows) is False


def test_load_thresholds(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"min": {"x": 1}}), encoding="utf-8")
    assert gate.load_thresholds(path)["min"]["x"] == 1


def test_shipped_thresholds_are_valid():
    for name in ("thresholds.json", "thresholds.smoke.json"):
        data = gate.load_thresholds(f"evals/{name}")
        assert data.get("min") or data.get("max")

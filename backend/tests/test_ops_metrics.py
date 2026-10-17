"""平台自身指标：渲染格式、转义与访问控制。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import ops_metrics
from app.core import metrics
from app.core.config import settings


def test_render_emits_prometheus_format_with_escaped_labels() -> None:
    metrics.reset()
    metrics.inc("aiops_unknown_connector_total", kind='a"b\nc')

    text = metrics.render()

    assert "# TYPE aiops_unknown_connector_total counter" in text
    assert 'kind="a\\"b\\nc"' in text
    assert "# TYPE aiops_process_start_time_seconds gauge" in text


def test_counters_are_aggregated() -> None:
    metrics.reset()
    metrics.inc("aiops_audit_write_failures_total", stage="dataset_query")
    metrics.inc("aiops_audit_write_failures_total", stage="dataset_query")

    text = metrics.render()
    assert 'aiops_audit_write_failures_total{stage="dataset_query"} 2' in text


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ops_metrics.router)
    return TestClient(app)


def test_endpoint_exposes_liveness_and_db(monkeypatch) -> None:
    metrics.reset()
    response = _client().get("/metrics")
    assert response.status_code == 200
    assert "aiops_up 1" in response.text
    assert "aiops_db_up" in response.text
    assert response.headers["content-type"].startswith("text/plain")


def test_endpoint_requires_token_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "metrics_token", "scrape-secret")
    client = _client()

    assert client.get("/metrics").status_code == 401
    ok = client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
    assert ok.status_code == 200

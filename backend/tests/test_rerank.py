"""检索重排：模式开关、API/LLM 路径、失败回退、打分解析。"""
from app.agent.diagnostics.knowledge import rerank as rr
from app.core.config import settings


def _docs():
    return [
        {"name": "a.md", "snippet": "AAA kafka lag", "score": 0.5},
        {"name": "b.md", "snippet": "BBB window", "score": 0.4},
        {"name": "c.md", "snippet": "CCC unrelated", "score": 0.3},
    ]


def test_mode_off_passthrough(monkeypatch):
    monkeypatch.setattr(settings, "rerank_mode", "off")
    docs = _docs()
    assert rr.rerank_documents("q", docs) == docs


def test_single_doc_passthrough(monkeypatch):
    monkeypatch.setattr(settings, "rerank_mode", "auto")
    docs = _docs()[:1]
    assert rr.rerank_documents("q", docs) == docs


def test_llm_rerank_reorders(monkeypatch):
    monkeypatch.setattr(settings, "rerank_mode", "llm")
    monkeypatch.setattr(settings, "rerank_base_url", "")
    docs = _docs()
    monkeypatch.setattr(rr, "_rerank_via_llm", lambda q, d: list(reversed(d)))
    out = rr.rerank_documents("q", docs)
    assert [d["name"] for d in out] == ["c.md", "b.md", "a.md"]


def test_api_rerank_reorders(monkeypatch):
    monkeypatch.setattr(settings, "rerank_mode", "api")
    monkeypatch.setattr(settings, "rerank_base_url", "http://x")
    docs = _docs()
    monkeypatch.setattr(rr, "_rerank_via_api", lambda q, d: [d[1], d[0], d[2]])
    out = rr.rerank_documents("q", docs)
    assert [d["name"] for d in out][:2] == ["b.md", "a.md"]


def test_failure_falls_back_to_original(monkeypatch):
    monkeypatch.setattr(settings, "rerank_mode", "llm")
    monkeypatch.setattr(settings, "rerank_base_url", "")

    def boom(_q, _d):
        raise RuntimeError("boom")

    monkeypatch.setattr(rr, "_rerank_via_llm", boom)
    docs = _docs()
    assert rr.rerank_documents("q", docs) == docs


def test_parse_scores():
    assert rr._parse_scores('```json\n[{"i":0,"s":9},{"i":1,"s":2}]\n```', 2) == {0: 9.0, 1: 2.0}
    assert rr._parse_scores("no json", 2) is None

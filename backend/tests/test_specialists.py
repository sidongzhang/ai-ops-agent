"""专家分工（多智能体）：角色选择、并行执行、单专家失败不影响整体。"""
from types import SimpleNamespace

import app.agent.diagnostics.investigator as investigator
from app.agent.diagnostics import specialists
from app.agent.diagnostics.tools import AgentDeps


class _FakeAgent:
    def __init__(self):
        self.prompts = []

    def run_sync(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return SimpleNamespace(output=f"结论: {prompt[:16]}")


def test_runs_selected_roles(monkeypatch):
    fake = _FakeAgent()
    monkeypatch.setattr(investigator, "get_evidence_agent", lambda: fake)
    deps = AgentDeps(descriptor={"name": "t", "services": []})
    out = specialists.run_specialists(deps, "看看 MySQL", ["infra", "metrics"])
    assert set(out) == {"infra", "metrics"}
    assert all("结论:" in v for v in out.values())
    assert len(fake.prompts) == 2


def test_invalid_roles_fall_back_to_all(monkeypatch):
    monkeypatch.setattr(investigator, "get_evidence_agent", lambda: _FakeAgent())
    deps = AgentDeps(descriptor={"name": "t"})
    out = specialists.run_specialists(deps, "x", ["nope"])
    assert set(out) == set(specialists.ROLE_ORDER)


def test_single_expert_failure_isolated(monkeypatch):
    class _Boom:
        def run_sync(self, *args, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(investigator, "get_evidence_agent", lambda: _Boom())
    deps = AgentDeps(descriptor={"name": "t"})
    out = specialists.run_specialists(deps, "x", ["logs"])
    assert "失败" in out["logs"]

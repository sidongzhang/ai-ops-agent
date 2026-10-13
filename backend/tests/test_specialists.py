"""专家分工（多智能体）：角色选择、并行执行、单专家失败不影响整体。

注意：pydantic-ai 2.55 起禁止在同步上下文里嵌套 `run_sync`（防死锁），
专家子代理统一走 `await agent.run(...)`，因此这里也用 async 断言。
"""
import asyncio
from types import SimpleNamespace

import app.agent.diagnostics.investigator as investigator
from app.agent.diagnostics import specialists
from app.agent.diagnostics.tools import AgentDeps


class _FakeAgent:
    def __init__(self):
        self.prompts = []

    async def run(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return SimpleNamespace(output=f"结论: {prompt[:16]}")


def _run(deps, task, roles=None):
    return asyncio.run(specialists.run_specialists(deps, task, roles))


def test_runs_selected_roles(monkeypatch):
    fake = _FakeAgent()
    monkeypatch.setattr(investigator, "get_evidence_agent", lambda: fake)
    deps = AgentDeps(descriptor={"name": "t", "services": []})
    out = _run(deps, "看看 MySQL", ["infra", "metrics"])
    assert set(out) == {"infra", "metrics"}
    assert all("结论:" in v for v in out.values())
    assert len(fake.prompts) == 2


def test_invalid_roles_fall_back_to_all(monkeypatch):
    monkeypatch.setattr(investigator, "get_evidence_agent", lambda: _FakeAgent())
    deps = AgentDeps(descriptor={"name": "t"})
    out = _run(deps, "x", ["nope"])
    assert set(out) == set(specialists.ROLE_ORDER)


def test_single_expert_failure_isolated(monkeypatch):
    class _Boom:
        async def run(self, *args, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(investigator, "get_evidence_agent", lambda: _Boom())
    deps = AgentDeps(descriptor={"name": "t"})
    out = _run(deps, "x", ["logs"])
    assert "失败" in out["logs"]


def test_subagent_inherits_cancel_token(monkeypatch):
    """子代理必须拿到同一个取消令牌（一个 token 管住主代理与所有子代理）。"""
    seen = {}

    class _Capture:
        async def run(self, prompt, **kwargs):
            seen["token"] = kwargs.get("cancellation_token")
            return SimpleNamespace(output="ok")

    monkeypatch.setattr(investigator, "get_evidence_agent", lambda: _Capture())
    token = object()
    deps = AgentDeps(descriptor={"name": "t"}, cancel_token=token)
    asyncio.run(specialists.run_specialists(deps, "x", ["infra"]))
    assert seen["token"] is token

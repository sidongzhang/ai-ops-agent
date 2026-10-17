import pytest
"""证据护栏（新契约）：不重跑模型，只对"有量化结论但无有效取证"做标记。

重构后的契约变化（相对旧版）：
  • 不改写答案文案，而是返回 `meta`（`flagged` / `reason` / `evidence_available`）；
  • "有工具调用"不够，还要求**成功的证据类工具 + 非空输出**；
    可用性检查（health check）不能证明一个无关的业务计数。
"""
from app.agent.harness.evidence import apply_evidence_guard, evidence_available


def test_flagged_when_quantitative_claim_without_evidence():
    text, meta = apply_evidence_guard("国庆期间共执行切割 0 次。", [])

    assert text == "国庆期间共执行切割 0 次。", "护栏不应改写答案文案"
    assert meta["has_claims"] is True
    assert meta["evidence_available"] is False
    assert meta["flagged"] is True
    assert meta["reason"] == "no_evidence_tool"


def test_not_flagged_when_dataset_evidence_present():
    calls = [
        {
            "tool": "query_business_dataset",
            "status": "success",
            "output": "共 93 次切割",
        }
    ]
    _text, meta = apply_evidence_guard("共 93 次切割。", calls)

    assert meta["evidence_tool_used"] is True
    assert meta["evidence_available"] is True
    assert meta["flagged"] is False


@pytest.mark.xfail(
    strict=True,
    reason="已知缺陷：list_services 也被算作 evidence_available，"
           "与 evidence.py 文档承诺（探活不能证明无关业务计数）不一致；待按 claim scope 细分证据要求",
)
def test_health_check_is_not_evidence_for_business_counts():
    """可用性检查不能证明业务计数——这是重构后特意收紧的一条。"""
    calls = [{"tool": "list_services", "status": "success", "output": "Redis: ok"}]
    _text, meta = apply_evidence_guard("共 93 次切割。", calls)

    # 探活算"尝试过取证工具"，但不足以支撑业务计数 → 仍要标记
    assert meta["evidence_tool_used"] is True
    assert meta["evidence_available"] is False
    assert meta["flagged"] is True
    assert meta["reason"] == "evidence_unavailable"


def test_disabled_guard_never_flags():
    _text, meta = apply_evidence_guard("共 93 次切割。", [], enabled=False)

    assert meta["flagged"] is False
    assert meta["verification"] == "not_performed"


def test_evidence_available_requires_success_and_output():
    assert evidence_available([{"tool": "query_business_dataset", "status": "failed", "output": "x"}]) is False
    assert evidence_available([{"tool": "query_business_dataset", "status": "success", "output": ""}]) is False
    assert evidence_available(
        [{"tool": "query_business_dataset", "status": "success", "output": "共 93 次"}]
    ) is True

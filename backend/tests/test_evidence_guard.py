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


def test_health_check_is_not_evidence_for_business_counts():
    """可用性检查不能证明业务计数（本次修复的核心场景）。"""
    calls = [{"tool": "list_services", "status": "success", "output": "Redis: ok"}]
    _text, meta = apply_evidence_guard("共 93 次切割。", calls)

    assert meta["evidence_tool_used"] is True, "确实调过工具"
    assert meta["evidence_scope"] == "data", "量化结论要求数据类证据"
    assert meta["evidence_available"] is False
    assert meta["flagged"] is True
    assert meta["reason"] == "evidence_not_data_bearing"


def test_non_quantitative_health_claim_is_covered_by_probe():
    """非量化结论（服务活着/挂了）由探活支撑即可，不应误伤。"""
    calls = [{"tool": "check_service", "status": "success", "output": "Redis: down"}]
    _text, meta = apply_evidence_guard("Redis 未运行。", calls)

    assert meta["has_claims"] is False
    assert meta["evidence_scope"] == "any"
    assert meta["flagged"] is False


def test_failed_data_tool_is_not_evidence():
    calls = [{"tool": "query_business_dataset", "status": "failed", "output": ""}]
    _text, meta = apply_evidence_guard("共 93 次切割。", calls)

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

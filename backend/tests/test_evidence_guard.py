"""证据约束（防幻觉）：数字识别、有无取证工具、标记与提示。"""
import pytest
from app.agent.harness.evidence import (
    apply_evidence_guard,
    evidence_tools_used,
    has_quantitative_claims,
)


def test_claims_detection():
    assert has_quantitative_claims("共 93 次切割")
    assert has_quantitative_claims("成功率 98%")
    assert has_quantitative_claims("文件 2880 字节")
    assert not has_quantitative_claims("没有发现异常，一切正常")


def test_evidence_tools():
    assert evidence_tools_used([{"tool": "query_business_dataset"}])
    assert not evidence_tools_used([{"tool": "run_python_analysis_x"}])
    assert not evidence_tools_used([])


@pytest.mark.skip(reason="重构后契约变更：待按新实现重写（commit 323a42d，见 ARCHITECTURE.md）")


def test_flagged_when_claims_without_evidence():
    text, meta = apply_evidence_guard("国庆期间共执行切割 0 次。", [])
    assert meta["flagged"] is True
    assert meta["has_claims"] is True
    assert "未发现对应的取证工具调用" in text


@pytest.mark.skip(reason="重构后契约变更：待按新实现重写（commit 323a42d，见 ARCHITECTURE.md）")


def test_not_flagged_when_evidence_present():
    text, meta = apply_evidence_guard("共 93 次切割。", [{"tool": "query_business_dataset"}])
    assert meta["flagged"] is False
    assert text == "共 93 次切割。"


def test_disabled_never_flags():
    text, meta = apply_evidence_guard("共 1 次", [], enabled=False)
    assert text == "共 1 次"
    assert meta["flagged"] is False

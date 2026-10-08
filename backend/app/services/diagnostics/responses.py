"""诊断响应的统一构建（service 主流程与降级路径共用）。"""
from app.schemas import DiagnoseResponse
from app.services.diagnostics.evidence import (
    build_evidence_from_tool_calls,
    build_evidence_steps,
)


def build_diagnose_response(
    *,
    report_id: int | None,
    system_id: int,
    status: str = "success",
    answer: str,
    template_name: str = "",
    template_description: str = "",
    model: str = "",
    duration_ms: int = 0,
    total_tokens: int = 0,
    evidence_sources: list[str] | None = None,
    tool_calls: list[dict] | None = None,
    evidence: list[dict] | None = None,
    evidence_steps: list[str] | None = None,
    knowledge_refs: list[dict] | None = None,
    error_message: str = "",
) -> DiagnoseResponse:
    tool_calls = tool_calls or []
    evidence = evidence or build_evidence_from_tool_calls(tool_calls)
    evidence_steps = evidence_steps or build_evidence_steps(tool_calls)
    return DiagnoseResponse(
        id=report_id,
        system_id=system_id,
        status=status,
        answer=answer,
        template_name=template_name,
        template_description=template_description,
        model=model,
        duration_ms=duration_ms,
        total_tokens=total_tokens,
        evidence_sources=evidence_sources or [],
        evidence_steps=evidence_steps,
        tool_calls=tool_calls,
        evidence=evidence,
        knowledge_refs=knowledge_refs or [],
        error_message=error_message,
    )


__all__ = ["build_diagnose_response"]

"""Analyze log files submitted by an external business system."""
from __future__ import annotations

import json
from uuid import uuid4

from sqlmodel import Session

from app.agent.diagnostics.runner import diagnose_with_details
from app.repositories.systems import list_enabled_services_for_system
from app.schemas.openapi import LogAnalysisResponse
from app.services.audit import record_audit_event
from app.services.descriptors.builder import system_to_descriptor
from app.services.systems.service import require_system

MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_LOG_CHARS = 180_000


def _format_log_content(raw: bytes) -> tuple[str, str, bool]:
    if len(raw) > MAX_LOG_BYTES:
        raise ValueError("日志文件不能超过 2 MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("日志文件必须使用 UTF-8 编码") from exc

    try:
        payload = json.loads(text)
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        log_format = "JSON"
    except json.JSONDecodeError:
        content = text
        log_format = "文本日志"

    truncated = len(content) > MAX_LOG_CHARS
    return content[:MAX_LOG_CHARS], log_format, truncated


def analyze_uploaded_log(
    session: Session,
    system_id: int,
    org_id: int,
    *,
    filename: str,
    raw: bytes,
    question: str,
    request_id: str,
    token_id: int,
) -> LogAnalysisResponse:
    system = require_system(session, system_id, org_id)
    content, log_format, truncated = _format_log_content(raw)
    request_id = request_id.strip() or f"log-{uuid4().hex}"
    user_question = question.strip() or "请分析这份日志，找出异常、可能原因、影响范围和建议的处理措施。"
    prompt = (
        "你正在分析外部系统上传的一份日志文件。只能依据日志内容和系统上下文作答，"
        "不要编造不存在的指标。请输出：1. 结论 2. 关键异常证据 3. 可能原因 4. 影响范围 "
        "5. 建议措施 6. 还需要人工确认的事项。\n\n"
        f"用户问题：{user_question}\n"
        f"文件名：{filename}\n"
        f"日志格式：{log_format}\n"
        f"日志内容：\n```\n{content}\n```"
    )
    descriptor = system_to_descriptor(
        system,
        list_enabled_services_for_system(session, system.id),
    )
    try:
        run = diagnose_with_details(
            descriptor,
            prompt,
            org_id=org_id,
            system_id=system.id,
            trace_question=f"外部日志文件分析：{filename}（原始内容不写入追踪）",
            skill_steps=(
                "## 排查剧本：外部日志文件分析\n"
                "1. 先判断日志是否包含明确错误、异常堆栈、超时或依赖失败。\n"
                "2. 按时间和频率区分偶发事件与持续故障。\n"
                "3. 给出有日志证据支持的原因和可执行措施。\n"
                "4. 无法确定时明确说明需要补充什么信息。"
            ),
        )
        response = LogAnalysisResponse(
            request_id=request_id,
            system_id=system.id,
            filename=filename,
            log_format=log_format,
            report=run.answer,
            model=run.model,
            duration_ms=run.duration_ms,
            truncated=truncated,
        )
        record_audit_event(
            session,
            org_id=org_id,
            system_id=system.id,
            actor_type="system_token",
            actor_id=token_id,
            event_type="openapi.log_analysis.completed",
            target_type="log_file",
            target_id=request_id,
            input={
                "request_id": request_id,
                "filename": filename,
                "log_format": log_format,
                "bytes": len(raw),
                "truncated": truncated,
                "question": user_question[:500],
            },
            output={"model": run.model, "duration_ms": run.duration_ms},
            commit=True,
        )
        return response
    except Exception as exc:
        record_audit_event(
            session,
            org_id=org_id,
            system_id=system.id,
            actor_type="system_token",
            actor_id=token_id,
            event_type="openapi.log_analysis.failed",
            target_type="log_file",
            target_id=request_id,
            status="failed",
            input={"request_id": request_id, "filename": filename, "bytes": len(raw)},
            output={"error": str(exc)[:1000]},
            commit=True,
        )
        raise

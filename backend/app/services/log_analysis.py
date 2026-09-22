"""Analyze log files submitted by an external business system."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlmodel import Session, select

from app.core.database import engine
from app.models.messages import SystemMessage
from app.models.tokens import SystemToken
from app.repositories.messages import find_message_by_request_id
from app.agent.diagnostics.runner import diagnose_with_details
from app.repositories.systems import list_enabled_services_for_system
from app.schemas.openapi import LogAnalysisAcceptedResponse, LogAnalysisResponse
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


def enqueue_uploaded_log_analysis(
    message_id: int,
    *,
    filename: str,
    log_format: str,
    content: str,
    question: str,
    truncated: bool,
    token_id: int,
) -> None:
    from app.workers.tasks import process_open_log_analysis

    process_open_log_analysis.delay(
        message_id,
        filename,
        log_format,
        content,
        question,
        truncated,
        token_id,
    )


def submit_uploaded_log_analysis(
    session: Session,
    system_id: int,
    org_id: int,
    *,
    filename: str,
    raw: bytes,
    question: str,
    request_id: str,
    token: SystemToken,
    message_type: str = "log_analysis",
    severity: str = "warning",
    notify_after_processing: bool = False,
) -> LogAnalysisAcceptedResponse:
    system = require_system(session, system_id, org_id)
    content, log_format, truncated = _format_log_content(raw)
    request_id = request_id.strip() or f"log-{uuid4().hex}"

    existing = find_message_by_request_id(session, system.id, request_id)
    if existing:
        existing_status = (existing.related or {}).get("processing_status", "queued")
        if existing.message_type in {"log_analysis", "alert"} and existing_status in {"failed", "enqueue_failed"}:
            user_question = question.strip() or "请分析这份日志，找出异常、可能原因、影响范围和建议的处理措施。"
            related = dict(existing.related or {})
            related.update({
                "filename": filename,
                "log_format": log_format,
                "bytes": len(raw),
                "truncated": truncated,
                "question": user_question[:500],
                "need_llm_process": True,
                "processing_status": "queued",
                "notify_after_processing": notify_after_processing,
                "processing_kind": "error_log_alert" if message_type == "alert" else "log_analysis",
            })
            related.pop("processing_error", None)
            related.pop("processing_failed_at", None)
            existing.related = related
            existing.message_type = message_type
            existing.severity = severity
            existing.title = f"{'错误日志告警' if message_type == 'alert' else '日志分析'}：{filename}"
            existing.summary = f"外部系统重新上传 {log_format} 日志，等待后台分析。"
            existing.diagnosis = "日志已重新接收，正在后台分析。请稍后在消息中心或开放接口查询结果。"
            existing.suggestion = ["稍后查询同一 request_id 的消息结果。", "如问题紧急，可先查看业务系统本地日志和最近监控指标。"]
            token.last_used_at = datetime.now(timezone.utc)
            session.add(existing)
            session.add(token)
            session.commit()
            session.refresh(existing)
            try:
                enqueue_uploaded_log_analysis(
                    existing.id,
                    filename=filename,
                    log_format=log_format,
                    content=content,
                    question=user_question,
                    truncated=truncated,
                    token_id=token.id,
                )
            except Exception as exc:
                related = dict(existing.related or {})
                related["processing_status"] = "enqueue_failed"
                related["processing_error"] = str(exc)
                existing.related = related
                session.add(existing)
                session.commit()
                session.refresh(existing)
            return LogAnalysisAcceptedResponse(
                request_id=request_id,
                system_id=system.id,
                message_id=existing.id,
                filename=filename,
                log_format=log_format,
                status=(existing.related or {}).get("processing_status", "queued"),
                truncated=truncated,
                detail="该 request_id 已重新接收，平台将再次后台分析。",
            )
        return LogAnalysisAcceptedResponse(
            request_id=request_id,
            system_id=system.id,
            message_id=existing.id,
            filename=(existing.related or {}).get("filename", filename),
            log_format=(existing.related or {}).get("log_format", log_format),
            status=existing_status,
            truncated=bool((existing.related or {}).get("truncated", truncated)),
            detail="该 request_id 已接收过，返回已有消息记录。",
        )

    user_question = question.strip() or "请分析这份日志，找出异常、可能原因、影响范围和建议的处理措施。"
    message = SystemMessage(
        org_id=system.org_id,
        system_id=system.id,
        message_type=message_type,
        severity=severity,
        title=f"{'错误日志告警' if message_type == 'alert' else '日志分析'}：{filename}",
        summary=f"外部系统上传 {log_format} 日志，等待后台分析。",
        content="原始日志不写入平台数据库；后台分析完成后会回写诊断报告。",
        diagnosis="日志已接收，正在后台分析。请稍后在消息中心或开放接口查询结果。",
        suggestion=["稍后查询同一 request_id 的消息结果。", "如问题紧急，可先查看业务系统本地日志和最近监控指标。"],
        source="openapi",
        related={
            "request_id": request_id,
            "token_id": token.id,
            "filename": filename,
            "log_format": log_format,
            "bytes": len(raw),
            "truncated": truncated,
            "question": user_question[:500],
            "need_llm_process": True,
            "processing_status": "queued",
            "notify_after_processing": notify_after_processing,
            "processing_kind": "error_log_alert" if message_type == "alert" else "log_analysis",
        },
    )
    token.last_used_at = datetime.now(timezone.utc)
    session.add(message)
    session.add(token)
    session.commit()
    session.refresh(message)

    record_audit_event(
        session,
        org_id=org_id,
        system_id=system.id,
        actor_type="system_token",
        actor_id=token.id,
        event_type="openapi.log_analysis.queued",
        target_type="message",
        target_id=message.id,
        input={
            "request_id": request_id,
            "filename": filename,
            "log_format": log_format,
            "bytes": len(raw),
            "truncated": truncated,
            "question": user_question[:500],
        },
        output={"message_id": message.id},
        commit=True,
    )

    try:
        enqueue_uploaded_log_analysis(
            message.id,
            filename=filename,
            log_format=log_format,
            content=content,
            question=user_question,
            truncated=truncated,
            token_id=token.id,
        )
    except Exception as exc:
        related = dict(message.related or {})
        related["processing_status"] = "enqueue_failed"
        related["processing_error"] = str(exc)
        message.related = related
        session.add(message)
        session.commit()
        session.refresh(message)

    return LogAnalysisAcceptedResponse(
        request_id=request_id,
        system_id=system.id,
        message_id=message.id,
        filename=filename,
        log_format=log_format,
        status=(message.related or {}).get("processing_status", "queued"),
        truncated=truncated,
    )


def process_uploaded_log_analysis(
    message_id: int,
    *,
    filename: str,
    log_format: str,
    content: str,
    question: str,
    truncated: bool,
    token_id: int,
    db_engine=None,
) -> dict:
    with Session(db_engine or engine) as session:
        message = session.exec(select(SystemMessage).where(SystemMessage.id == message_id)).first()
        if not message:
            return {"ok": False, "message_id": message_id, "error": "message_not_found"}
        try:
            response = _run_log_analysis(
                session,
                message.system_id,
                message.org_id,
                filename=filename,
                log_format=log_format,
                content=content,
                question=question,
                request_id=(message.related or {}).get("request_id", f"log-{message.id}"),
                token_id=token_id,
                raw_size=(message.related or {}).get("bytes", 0),
                truncated=truncated,
            )
            related = dict(message.related or {})
            related["processing_status"] = "done"
            related["processing_mode"] = "agent"
            related["processed_at"] = datetime.now(timezone.utc).isoformat()
            related["model"] = response.model
            related["duration_ms"] = response.duration_ms
            message.related = related
            message.summary = response.report[:160]
            message.diagnosis = response.report
            if message.message_type == "alert":
                message.suggestion = [
                    "优先确认错误日志对应功能是否仍在持续失败。",
                    "按分析报告中的原因检查依赖服务、配置、网络和最近发布变更。",
                    "如影响生产，请在消息中心确认告警并推进处理。"
                ]
            else:
                message.suggestion = ["按报告中的建议处理；如涉及服务异常，请继续结合健康检查、指标和日志确认。"]
            session.add(message)
            session.commit()
            if related.get("notify_after_processing"):
                from app.services.notifications.alerts import deliver_message_notifications
                from app.services.systems.service import require_system

                system = require_system(session, message.system_id, message.org_id)
                deliver_message_notifications(session, system, message, actor_id=str(token_id))
            return {"ok": True, "message_id": message.id}
        except Exception as exc:
            related = dict(message.related or {})
            related["processing_status"] = "failed"
            related["processing_error"] = str(exc)
            related["processing_failed_at"] = datetime.now(timezone.utc).isoformat()
            message.related = related
            message.diagnosis = f"日志分析失败：{exc}"
            message.suggestion = ["请稍后重试，或先在业务系统本地查看该日志文件。"]
            session.add(message)
            session.commit()
            record_audit_event(
                session,
                org_id=message.org_id,
                system_id=message.system_id,
                actor_type="system_token",
                actor_id=token_id,
                event_type="openapi.log_analysis.processing_failed",
                target_type="message",
                target_id=message.id,
                status="failed",
                input={"request_id": (message.related or {}).get("request_id"), "filename": filename},
                output={"error": str(exc)[:1000]},
                commit=True,
            )
            return {"ok": False, "message_id": message.id, "error": str(exc)}


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
    return _run_log_analysis(
        session,
        system.id,
        org_id,
        filename=filename,
        log_format=log_format,
        content=content,
        question=user_question,
        request_id=request_id,
        token_id=token_id,
        raw_size=len(raw),
        truncated=truncated,
    )


def _run_log_analysis(
    session: Session,
    system_id: int,
    org_id: int,
    *,
    filename: str,
    log_format: str,
    content: str,
    question: str,
    request_id: str,
    token_id: int,
    raw_size: int,
    truncated: bool,
) -> LogAnalysisResponse:
    system = require_system(session, system_id, org_id)
    prompt = (
        "你正在分析外部系统上传的一份日志文件。只能依据日志内容和系统上下文作答，"
        "不要编造不存在的指标。请输出：1. 结论 2. 关键异常证据 3. 可能原因 4. 影响范围 "
        "5. 建议措施 6. 还需要人工确认的事项。\n\n"
        f"用户问题：{question}\n"
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
                "bytes": raw_size,
                "truncated": truncated,
                "question": question[:500],
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
            input={"request_id": request_id, "filename": filename, "bytes": raw_size},
            output={"error": str(exc)[:1000]},
            commit=True,
        )
        raise

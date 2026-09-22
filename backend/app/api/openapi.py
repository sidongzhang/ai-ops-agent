"""External business system OpenAPI."""
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from sqlmodel import Session

from app.core.database import get_session
from app.core.security import hash_system_token
from app.models.systems import MonitoredSystem
from app.models.tokens import SystemToken
from app.repositories.systems import get_system_by_key
from app.repositories.tokens import get_token_by_hash
from app.schemas.messages import SystemMessageOut
from app.schemas.diagnostics import DiagnoseResponse, DiagnosisReportOut
from app.schemas.openapi import (
    LogAnalysisAcceptedResponse,
    OpenAlertIn,
    OpenDiagnosisIn,
    OpenHealthIn,
    OpenMessageIn,
    OpenMessagePageOut,
    OpenMessageStatusIn,
    OpenProductionReportIn,
)
from app.services.openapi import (
    get_open_message_by_request_id,
    list_open_messages,
    submit_open_alert,
    submit_open_health,
    submit_open_message,
    submit_open_production_report,
    update_open_message_status,
)
from app.services.log_analysis import submit_uploaded_log_analysis
from app.services.diagnostics.service import complete_diagnosis_report, start_diagnosis
from app.services.diagnostics.reports import get_diagnosis_report, list_diagnosis_reports

router = APIRouter(prefix="/openapi/v1", tags=["openapi"])


@dataclass
class OpenApiIdentity:
    system: MonitoredSystem
    token: SystemToken


def require_openapi_identity(
    request: Request,
    authorization: str = Header(..., alias="Authorization"),
    system_code: str = Header(..., alias="X-System-Code"),
    session: Session = Depends(get_session),
) -> OpenApiIdentity:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少 Bearer Token")
    raw_token = authorization.removeprefix("Bearer ").strip()
    token = get_token_by_hash(session, hash_system_token(raw_token))
    if not token or token.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效或已禁用的系统 Token")
    if token.expires_at and token.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "系统 Token 已过期")

    system = get_system_by_key(session, token.org_id, system_code)
    if not system or system.id != token.system_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "系统编码与 Token 不匹配")

    if token.allowed_ips:
        client_ip = request.client.host if request.client else ""
        if client_ip not in token.allowed_ips:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "来源 IP 不在允许范围内")

    return OpenApiIdentity(system=system, token=token)


def require_scope(identity: OpenApiIdentity, scope: str) -> None:
    if scope not in (identity.token.scopes or []):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Token 缺少权限: {scope}")


def require_any_scope(identity: OpenApiIdentity, scopes: list[str]) -> None:
    if not any(scope in (identity.token.scopes or []) for scope in scopes):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Token 缺少权限: {' / '.join(scopes)}")


def _rollup(rows: list[dict], count_key: str, bytes_key: str = "") -> str:
    """把明细行压成一行汇总，避免把大数组原样喂给模型。"""
    total = sum(int(r.get(count_key) or 0) for r in rows)
    parts = [f"{total} 项"]
    if bytes_key:
        total_bytes = sum(int(r.get(bytes_key) or 0) for r in rows)
        parts.append(f"共 {total_bytes} 字节")
        empty = sum(
            int(r.get(count_key) or 0) for r in rows if not int(r.get(bytes_key) or 0)
        )
        if empty:
            parts.append(f"其中 {empty} 项字节数为 0")
    groups: list[str] = []
    for row in rows:
        label = row.get("stage") or row.get("satellite") or row.get("service_id") or row.get("data_type")
        if label:
            groups.append(str(label))
    if groups:
        parts.append("涉及 " + "、".join(dict.fromkeys(groups)))
    return "，".join(parts)


def _compact_business_context(context: dict) -> dict:
    """压缩 ALGP 任务快照。

    `recent_abnormal_runs` / `file_summary` / `artifact_summary` 这类明细数组
    占了快照体积的 60%~75%，但它们几乎每轮都会被原样重发给模型。
    这里把它们折成汇总；明细仍完整保存在 diagnosis_reports.business_context 里，
    需要时模型可以用 query_business_dataset 按需查询。
    """
    compact = dict(context)

    abnormal = compact.get("recent_abnormal_runs")
    if isinstance(abnormal, list) and abnormal:
        statuses: dict[str, int] = {}
        search_types: dict[str, int] = {}
        for row in abnormal:
            statuses[str(row.get("status") or "-")] = statuses.get(str(row.get("status") or "-"), 0) + 1
            search_types[str(row.get("search_type") or "-")] = search_types.get(str(row.get("search_type") or "-"), 0) + 1
        compact["recent_abnormal_runs"] = {
            "sampled": len(abnormal),
            "note": "仅为最近若干条未完成运行的抽样，非全量；总量与明细请用 stuck_runs 数据集查询",
            "by_status": statuses,
            "by_search_type": search_types,
            "sample_run_ids": [str(r.get("run_id") or "") for r in abnormal[:3]],
        }

    for key, count_key, bytes_key in (
        ("file_summary", "file_count", "total_bytes"),
        ("artifact_summary", "artifact_count", "total_bytes"),
    ):
        rows = compact.get(key)
        # 这两个摘要与该 run 的根因直接相关，保留明细；压掉反而逼模型多查一轮数据集，
        # 而每多一轮就要重发整段上下文，得不偿失。
        if isinstance(rows, list) and len(rows) > 8:
            compact[key] = rows[:8]

    return compact


def _external_diagnosis_question(
    question: str,
    message: SystemMessageOut | None,
    business_context: dict | None = None,
) -> str:
    lines = [question]
    if message:
        related = message.related or {}
        lines.extend([
            "",
            "以下是当前问题关联的 ALGP 消息，请结合实时健康检查、日志、指标和业务数据进行诊断：",
            f"- 请求 ID：{related.get('request_id') or '-'}",
            f"- 标题：{message.title or '-'}",
            f"- 类型：{message.message_type or '-'}",
            f"- 严重级别：{message.severity or '-'}",
            f"- 内容：{(message.content or '')[:6000]}",
            f"- 初步判断：{(message.diagnosis or '')[:2000]}",
        ])
    if business_context:
        context_json = json.dumps(
            _compact_business_context(business_context), ensure_ascii=False, default=str
        )[:8000]
        lines.extend([
            "",
            "以下是 ALGP 在发起诊断时采集的只读任务快照摘要（明细已折叠，需要具体数字时"
            "请用 query_business_dataset 查对应数据集）。它是本次诊断的业务证据；"
            "若 run_match.matched=false，必须明确说明未匹配到具体运行实例，不得把 recent_abnormal_runs 当作当前任务：",
            context_json,
        ])
    return "\n".join(lines)[:18000]


@router.post("/diagnoses", response_model=DiagnoseResponse, status_code=status.HTTP_202_ACCEPTED)
def create_external_diagnosis(
    body: OpenDiagnosisIn,
    background_tasks: BackgroundTasks,
    identity: OpenApiIdentity = Depends(require_openapi_identity),
    session: Session = Depends(get_session),
):
    require_scope(identity, "diagnosis:run")
    message = None
    if body.request_id:
        try:
            message = get_open_message_by_request_id(session, identity.system, body.request_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc))
    actor_id = f"openapi:{identity.token.id}:{body.actor_id or 'unknown'}"
    started = start_diagnosis(
        session,
        identity.system.id,
        identity.system.org_id,
        body.question.strip(),
        actor_id=actor_id,
        external_request_id=body.request_id,
        business_context=body.business_context,
    )
    if started.id is None:
        raise HTTPException(500, "诊断任务创建失败")
    identity.token.last_used_at = datetime.now(timezone.utc)
    session.add(identity.token)
    session.commit()
    background_tasks.add_task(
        complete_diagnosis_report,
        started.id,
        identity.system.id,
        identity.system.org_id,
        _external_diagnosis_question(body.question.strip(), message, body.business_context),
        actor_id=actor_id,
        model_mode=body.model_mode,
    )
    return started


@router.get("/diagnoses", response_model=list[DiagnosisReportOut])
def query_external_diagnoses(
    request_id: str = "",
    limit: int = Query(default=20, ge=1, le=100),
    identity: OpenApiIdentity = Depends(require_openapi_identity),
    session: Session = Depends(get_session),
):
    require_scope(identity, "diagnosis:read")
    return list_diagnosis_reports(
        session,
        identity.system.id,
        identity.system.org_id,
        limit=limit,
        external_request_id=request_id,
    )


@router.get("/diagnoses/{report_id}", response_model=DiagnosisReportOut)
def query_external_diagnosis(
    report_id: int,
    identity: OpenApiIdentity = Depends(require_openapi_identity),
    session: Session = Depends(get_session),
):
    require_scope(identity, "diagnosis:read")
    try:
        return get_diagnosis_report(
            session,
            identity.system.id,
            identity.system.org_id,
            report_id,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post("/log-analysis", response_model=LogAnalysisAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def analyze_log_file(
    file: UploadFile = File(..., description="log.json 或文本日志，最大 2 MB"),
    question: str = Form(default=""),
    request_id: str = Form(default=""),
    identity: OpenApiIdentity = Depends(require_openapi_identity),
    session: Session = Depends(get_session),
):
    require_scope(identity, "log:analyze")
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请上传日志文件")
    try:
        raw = await file.read()
        return submit_uploaded_log_analysis(
            session,
            identity.system.id,
            identity.system.org_id,
            filename=file.filename,
            raw=raw,
            question=question,
            request_id=request_id,
            token=identity.token,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"日志接收失败：{exc}")


@router.post("/error-logs", response_model=LogAnalysisAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_error_log_alert(
    file: UploadFile = File(..., description="错误日志 JSON 或文本，最大 2 MB"),
    question: str = Form(default="请分析这份错误日志，生成告警、错误原因和处理建议。"),
    request_id: str = Form(default=""),
    identity: OpenApiIdentity = Depends(require_openapi_identity),
    session: Session = Depends(get_session),
):
    require_any_scope(identity, ["log:alert", "log:analyze"])
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请上传错误日志文件")
    try:
        raw = await file.read()
        return submit_uploaded_log_analysis(
            session,
            identity.system.id,
            identity.system.org_id,
            filename=file.filename,
            raw=raw,
            question=question,
            request_id=request_id,
            token=identity.token,
            message_type="alert",
            severity="error",
            notify_after_processing=True,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"错误日志接收失败：{exc}")


@router.post("/alerts", response_model=SystemMessageOut, status_code=status.HTTP_201_CREATED)
def create_alert(
    body: OpenAlertIn,
    identity: OpenApiIdentity = Depends(require_openapi_identity),
    session: Session = Depends(get_session),
):
    require_scope(identity, "alert:create")
    return submit_open_alert(session, identity.system, identity.token, body)


@router.post("/messages", response_model=SystemMessageOut, status_code=status.HTTP_201_CREATED)
def create_message(
    body: OpenMessageIn,
    identity: OpenApiIdentity = Depends(require_openapi_identity),
    session: Session = Depends(get_session),
):
    require_scope(identity, "message:send")
    return submit_open_message(session, identity.system, identity.token, body, message_type="message")


@router.post("/reports/daily", response_model=SystemMessageOut, status_code=status.HTTP_201_CREATED)
def create_daily_report(
    body: OpenMessageIn,
    identity: OpenApiIdentity = Depends(require_openapi_identity),
    session: Session = Depends(get_session),
):
    require_scope(identity, "report:submit")
    return submit_open_message(session, identity.system, identity.token, body, message_type="daily_report")


@router.post("/reports/weekly", response_model=SystemMessageOut, status_code=status.HTTP_201_CREATED)
def create_weekly_report(
    body: OpenMessageIn,
    identity: OpenApiIdentity = Depends(require_openapi_identity),
    session: Session = Depends(get_session),
):
    require_scope(identity, "report:submit")
    return submit_open_message(session, identity.system, identity.token, body, message_type="weekly_report")


@router.post("/production/daily", response_model=SystemMessageOut, status_code=status.HTTP_201_CREATED)
def create_daily_production_report(
    body: OpenProductionReportIn,
    identity: OpenApiIdentity = Depends(require_openapi_identity),
    session: Session = Depends(get_session),
):
    require_scope(identity, "report:submit")
    return submit_open_production_report(session, identity.system, identity.token, body, message_type="daily_report")


@router.post("/production/weekly", response_model=SystemMessageOut, status_code=status.HTTP_201_CREATED)
def create_weekly_production_report(
    body: OpenProductionReportIn,
    identity: OpenApiIdentity = Depends(require_openapi_identity),
    session: Session = Depends(get_session),
):
    require_scope(identity, "report:submit")
    return submit_open_production_report(session, identity.system, identity.token, body, message_type="weekly_report")


@router.post("/reports/monthly", response_model=SystemMessageOut, status_code=status.HTTP_201_CREATED)
def create_monthly_report(
    body: OpenMessageIn,
    identity: OpenApiIdentity = Depends(require_openapi_identity),
    session: Session = Depends(get_session),
):
    require_scope(identity, "report:submit")
    return submit_open_message(session, identity.system, identity.token, body, message_type="monthly_report")


@router.get("/reports", response_model=OpenMessagePageOut)
def query_reports(
    report_type: str | None = Query(default=None, description="daily / weekly / monthly；为空返回全部报告"),
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: OpenApiIdentity = Depends(require_openapi_identity),
    session: Session = Depends(get_session),
):
    require_any_scope(identity, ["message:read", "report:read"])
    message_type = None
    if report_type:
        mapping = {
            "daily": "daily_report",
            "weekly": "weekly_report",
            "monthly": "monthly_report",
            "daily_report": "daily_report",
            "weekly_report": "weekly_report",
            "monthly_report": "monthly_report",
        }
        if report_type not in mapping:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "report_type 仅支持 daily / weekly / monthly")
        message_type = mapping[report_type]
    return list_open_messages(
        session,
        identity.system,
        status=status,
        message_type=message_type,
        limit=limit,
        offset=offset,
    )


@router.post("/health", response_model=dict)
def push_health(
    body: OpenHealthIn,
    identity: OpenApiIdentity = Depends(require_openapi_identity),
    session: Session = Depends(get_session),
):
    require_scope(identity, "health:push")
    return submit_open_health(session, identity.system, identity.token, body)


@router.get("/messages", response_model=OpenMessagePageOut)
def query_messages(
    status: str | None = None,
    message_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: OpenApiIdentity = Depends(require_openapi_identity),
    session: Session = Depends(get_session),
):
    require_scope(identity, "message:read")
    return list_open_messages(
        session,
        identity.system,
        status=status,
        message_type=message_type,
        limit=limit,
        offset=offset,
    )


@router.get("/messages/{request_id}", response_model=SystemMessageOut)
def query_message_by_request_id(
    request_id: str,
    identity: OpenApiIdentity = Depends(require_openapi_identity),
    session: Session = Depends(get_session),
):
    require_scope(identity, "message:read")
    try:
        return get_open_message_by_request_id(session, identity.system, request_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post("/messages/{request_id}/status", response_model=SystemMessageOut)
def update_message_status_by_request_id(
    request_id: str,
    body: OpenMessageStatusIn,
    identity: OpenApiIdentity = Depends(require_openapi_identity),
    session: Session = Depends(get_session),
):
    require_scope(identity, "message:write")
    try:
        return update_open_message_status(session, identity.system, identity.token, request_id, body.status)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

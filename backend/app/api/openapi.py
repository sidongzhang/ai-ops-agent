"""External business system OpenAPI."""
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from sqlmodel import Session

from app.core.database import get_session
from app.core.security import hash_system_token
from app.models.systems import MonitoredSystem
from app.models.tokens import SystemToken
from app.repositories.systems import get_system_by_key
from app.repositories.tokens import get_token_by_hash
from app.schemas.messages import SystemMessageOut
from app.schemas.openapi import LogAnalysisResponse, OpenAlertIn, OpenHealthIn, OpenMessageIn
from app.services.openapi import (
    get_open_message_by_request_id,
    list_open_messages,
    submit_open_alert,
    submit_open_health,
    submit_open_message,
)
from app.services.log_analysis import analyze_uploaded_log

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


@router.post("/log-analysis", response_model=LogAnalysisResponse)
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
        return analyze_uploaded_log(
            session,
            identity.system.id,
            identity.system.org_id,
            filename=file.filename,
            raw=raw,
            question=question,
            request_id=request_id,
            token_id=identity.token.id,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"日志分析失败：{exc}")


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


@router.post("/reports/monthly", response_model=SystemMessageOut, status_code=status.HTTP_201_CREATED)
def create_monthly_report(
    body: OpenMessageIn,
    identity: OpenApiIdentity = Depends(require_openapi_identity),
    session: Session = Depends(get_session),
):
    require_scope(identity, "report:submit")
    return submit_open_message(session, identity.system, identity.token, body, message_type="monthly_report")


@router.post("/health", response_model=dict)
def push_health(
    body: OpenHealthIn,
    identity: OpenApiIdentity = Depends(require_openapi_identity),
    session: Session = Depends(get_session),
):
    require_scope(identity, "health:push")
    return submit_open_health(session, identity.system, identity.token, body)


@router.get("/messages", response_model=list[SystemMessageOut])
def query_messages(
    status: str | None = None,
    message_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
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

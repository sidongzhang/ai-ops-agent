"""FastAPI 依赖：会话 + 当前用户/租户 + 当前采集器。租户隔离的统一入口。"""
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session, select

from .db import get_session
from .models import Collector, User
from .security import decode_access_token, hash_collector_key

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User:
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效或过期的凭据")
    user = session.get(User, int(payload["sub"]))
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    return user


def get_current_org_id(user: User = Depends(get_current_user)) -> int:
    return user.org_id


def get_current_collector(
    x_collector_key: str = Header(..., alias="X-Collector-Key"),
    session: Session = Depends(get_session),
) -> Collector:
    """采集器侧鉴权：凭 X-Collector-Key 头识别采集器（与用户 JWT 分离）。"""
    collector = session.exec(
        select(Collector).where(Collector.token_hash == hash_collector_key(x_collector_key))
    ).first()
    if not collector:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效的采集器密钥")
    return collector

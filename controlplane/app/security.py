"""密码哈希 + JWT + 采集器密钥。后续可平滑替换为 fastapi-users。

直接用 bcrypt（避开 passlib 1.7 与 bcrypt 4.x 的版本不兼容）。
bcrypt 上限 72 字节，统一截断处理。
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt

from .config import settings


def hash_password(password: str) -> str:
    pw = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: int, org_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": str(user_id), "org": org_id, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


# ── 采集器密钥（高熵随机串，存 sha256）──
def generate_collector_key() -> str:
    return secrets.token_urlsafe(32)


def hash_collector_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()

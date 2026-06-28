"""密码哈希 + JWT + 采集器密钥 + 连接器凭据字段加密。

直接用 bcrypt（避开 passlib 1.7 与 bcrypt 4.x 的版本不兼容）。
bcrypt 上限 72 字节，统一截断处理。

凭据加密（Fernet AES-128-CBC + HMAC-SHA256）：
- ENCRYPTION_KEY 未设置时跳过（dev 模式零配置）
- 加密值以 "enc:" 前缀标识，支持明文/密文并存（向后兼容）
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from cryptography.fernet import Fernet
from jose import JWTError, jwt

from .config import settings

# 凭据字段白名单：字段名（小写）包含以下词即视为敏感
_SENSITIVE_KEYS = {"password", "secret", "token", "private_key",
                   "identity_file", "auth_header", "kubeconfig", "api_key", "key"}
_ENC_PREFIX = "enc:"


def _get_fernet() -> Optional[Fernet]:
    key = settings.encryption_key
    return Fernet(key.encode()) if key else None


def _is_sensitive(field_name: str) -> bool:
    name = field_name.lower()
    return any(k in name for k in _SENSITIVE_KEYS)


def encrypt_sensitive_fields(config: dict) -> dict:
    """落库前加密 config/infra 中的敏感字段。dev 无密钥时原样返回。"""
    f = _get_fernet()
    if not f or not config:
        return config
    result = {}
    for k, v in config.items():
        if _is_sensitive(k) and v and not str(v).startswith(_ENC_PREFIX):
            result[k] = _ENC_PREFIX + f.encrypt(str(v).encode()).decode()
        else:
            result[k] = v
    return result


def decrypt_sensitive_fields(config: dict) -> dict:
    """连接器使用前解密。无密钥或无 enc: 前缀时原样返回（向后兼容明文）。"""
    f = _get_fernet()
    if not config:
        return config
    result = {}
    for k, v in config.items():
        if f and _is_sensitive(k) and isinstance(v, str) and v.startswith(_ENC_PREFIX):
            result[k] = f.decrypt(v[len(_ENC_PREFIX):].encode()).decode()
        else:
            result[k] = v
    return result


def mask_sensitive_fields(config: dict) -> dict:
    """API 响应中将有值的敏感字段替换为 '***'，防止凭据通过 API 泄露。"""
    if not config:
        return config
    return {k: ("***" if _is_sensitive(k) and v else v) for k, v in config.items()}


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

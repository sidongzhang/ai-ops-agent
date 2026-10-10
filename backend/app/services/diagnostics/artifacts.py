"""诊断附件落盘：把 agent 取回的归档文件存成可下载的 token 链接。

文件名形如 <token>__<原始文件名>；token 为 32 位十六进制，不可猜；
下载路由凭 token 免登录访问（见 api/diagnose.py 的 diagnosis-artifacts）。
"""
import os
import re
import uuid
from pathlib import Path

from app.core.config import settings

_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


def _artifact_dir() -> Path:
    base = settings.diagnosis_artifact_dir or os.path.join(settings.repo_root, ".dev-stack", "artifacts")
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(file_name: str) -> str:
    name = os.path.basename(str(file_name or "file"))
    name = re.sub(r"[^\w.\-]", "_", name)
    return name[:128] or "file"


def save_artifact(file_name: str, content: bytes) -> str:
    """写入附件，返回 token。"""
    token = uuid.uuid4().hex
    (_artifact_dir() / f"{token}__{_safe_name(file_name)}").write_bytes(content)
    return token


def find_artifact(token: str) -> tuple[Path, str] | None:
    """按 token 找附件，返回 (路径, 原始文件名)；非法 token 返回 None。"""
    if not _TOKEN_RE.match(str(token or "")):
        return None
    for path in _artifact_dir().glob(f"{token}__*"):
        if path.is_file():
            return path, path.name.split("__", 1)[1]
    return None

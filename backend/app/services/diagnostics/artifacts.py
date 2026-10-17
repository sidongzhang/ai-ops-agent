"""诊断附件落盘：把 agent 取回的归档文件存成可下载的 token 链接。

文件名形如 `<token>__<原始文件名>`；token 为 32 位十六进制（128 bit），不可猜。
同目录写 `<token>.meta.json` 记录 org / system / 创建时间：

- 下载链接是**能力 URL**（凭 token 免登录，和 S3 预签名链接同一模型）；
- 但能力必须有边界：TTL 到期即失效，且 system_id 必须与签发时一致；
- 无元数据的旧附件按 fail-closed 处理（不再放行）——旧实现没有租户信息可校验。
"""
import json
import os
import re
import time
import uuid
from pathlib import Path

from app.core.config import settings

_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_DEFAULT_TTL_HOURS = 168


def _artifact_dir() -> Path:
    base = settings.diagnosis_artifact_dir or os.path.join(settings.repo_root, ".dev-stack", "artifacts")
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(file_name: str) -> str:
    name = os.path.basename(str(file_name or "file"))
    name = re.sub(r"[^\w.\-]", "_", name)
    return name[:128] or "file"


def _ttl_seconds() -> float:
    hours = getattr(settings, "artifact_ttl_hours", _DEFAULT_TTL_HOURS)
    try:
        hours = float(hours)
    except (TypeError, ValueError):
        hours = _DEFAULT_TTL_HOURS
    return max(0.0, hours) * 3600


def _meta_path(token: str) -> Path:
    return _artifact_dir() / f"{token}.meta.json"


def purge_expired() -> int:
    """清理过期附件，返回删除的附件数（best-effort，失败不影响主流程）。"""
    removed = 0
    try:
        ttl = _ttl_seconds()
        now = time.time()
        for meta in _artifact_dir().glob("*.meta.json"):
            token = meta.name[: -len(".meta.json")]
            try:
                created = float(json.loads(meta.read_text(encoding="utf-8")).get("created_at", 0))
            except Exception:  # noqa: BLE001 - 坏元数据按过期处理
                created = 0
            if ttl and now - created <= ttl:
                continue
            for path in _artifact_dir().glob(f"{token}__*"):
                path.unlink(missing_ok=True)
                removed += 1
            meta.unlink(missing_ok=True)

        # 旧实现留下的无元数据附件：按 mtime 判断过期（否则永远无人清理）
        for path in _artifact_dir().iterdir():
            if "__" not in path.name or not path.is_file():
                continue
            token = path.name.split("__", 1)[0]
            if (_artifact_dir() / f"{token}.meta.json").exists():
                continue
            try:
                if ttl and now - path.stat().st_mtime <= ttl:
                    continue
                path.unlink()
                removed += 1
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        pass
    return removed


def save_artifact(file_name: str, content: bytes, *, org_id: int, system_id: int) -> str:
    """写入附件并登记租户信息，返回 token。"""
    token = uuid.uuid4().hex
    name = _safe_name(file_name)
    (_artifact_dir() / f"{token}__{name}").write_bytes(content)
    _meta_path(token).write_text(
        json.dumps(
            {
                "org_id": int(org_id),
                "system_id": int(system_id),
                "name": name,
                "bytes": len(content),
                "created_at": time.time(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    purge_expired()
    return token


def find_artifact(token: str, *, system_id: int | None = None) -> tuple[Path, str] | None:
    """按 token 找附件，返回 (路径, 原始文件名)。

    校验：token 形状 → 元数据存在 → 未过期 → system_id 一致（传入时）。
    任一条不满足返回 None（调用方统一 404，不泄露"存在但无权"）。
    """
    if not _TOKEN_RE.match(str(token or "")):
        return None
    meta_path = _meta_path(token)
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None

    ttl = _ttl_seconds()
    created = float(meta.get("created_at", 0) or 0)
    if ttl and time.time() - created > ttl:
        return None
    if system_id is not None and int(meta.get("system_id", -1)) != int(system_id):
        return None

    for path in _artifact_dir().glob(f"{token}__*"):
        if path.is_file():
            return path, path.name.split("__", 1)[1]
    return None

#!/usr/bin/env python3
"""数据库备份：优先 `pg_dump`，不可用时退化为 SQLAlchemy JSONL 导出。

产物：`<out>/<UTC 时间戳>/`
  - `manifest.json`：库标识（已脱敏）、表行数、每个文件的 sha256；
  - `dump.pgc`：pg_dump 自定义格式（可用 `pg_restore` 恢复）；
  - 或 `tables/*.jsonl.gz`：逐表导出（可用 `scripts/restore_db.py` 回灌）。

用法：
  python scripts/backup_db.py                        # 读取 .env 的 DATABASE_URL
  python scripts/backup_db.py --keep 30              # 保留最近 30 份
  python scripts/backup_db.py --database-url sqlite:///backend/dev.db

退出码：0 成功；1 失败（失败目录保留为 `<时间戳>.partial` 以便排查）。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (REPO_ROOT, REPO_ROOT / "backend"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

DEFAULT_OUT = REPO_ROOT / "backups"
DEFAULT_KEEP = 14
LOCK_STALE_SECONDS = 6 * 3600
TS_FORMAT = "%Y%m%d-%H%M%S"


def mask_url(url: str) -> str:
    """日志/manifest 里不落密码。"""
    if "@" not in url:
        return url
    head, tail = url.rsplit("@", 1)
    if "://" not in head:
        return url
    scheme, creds = head.split("://", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{tail}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_postgres(url: str) -> bool:
    return url.startswith("postgresql") or url.startswith("postgres://")


def _pg_conn_args(url: str) -> tuple[list[str], dict[str, str]]:
    """把 SQLAlchemy URL 拆成 pg_dump 参数 + PGPASSWORD（避免密码出现在进程列表）。"""
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    args = ["-h", parsed.host or "localhost", "-p", str(parsed.port or 5432)]
    if parsed.username:
        args += ["-U", parsed.username]
    if parsed.database:
        args += ["-d", parsed.database]
    env = dict(os.environ)
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
    return args, env


def backup_with_pg_dump(url: str, dest: Path) -> None:
    target = dest / "dump.pgc"
    args, env = _pg_conn_args(url)
    cmd = [
        "pg_dump",
        "--no-owner",
        "--no-privileges",
        "--format=custom",
        "--file",
        str(target),
        *args,
    ]
    subprocess.run(cmd, check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def backup_with_sqlalchemy(url: str, dest: Path) -> dict[str, int]:
    """逐表导出为 JSONL.gz；返回 {表名: 行数}。"""
    from sqlalchemy import create_engine, inspect, text

    engine = create_engine(url)
    tables_dir = dest / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    with engine.connect() as conn:
        names = sorted(inspect(conn).get_table_names())
        for name in names:
            quoted = conn.dialect.identifier_preparer.quote(name)
            rows = 0
            out_path = tables_dir / f"{name}.jsonl.gz"
            with gzip.open(out_path, "wt", encoding="utf-8") as fh:
                result = conn.execution_options(stream_results=True).execute(
                    text(f"SELECT * FROM {quoted}")
                )
                for row in result.mappings():
                    fh.write(json.dumps(dict(row), ensure_ascii=False, default=str) + "\n")
                    rows += 1
            counts[name] = rows
    return counts


def write_manifest(dest: Path, *, url: str, fmt: str, tables: dict[str, int] | None) -> Path:
    files = {}
    for path in sorted(dest.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            files[str(path.relative_to(dest))] = {
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": mask_url(url),
        "format": fmt,
        "tables": tables or {},
        "files": files,
        "tool": "pg_dump" if fmt == "pg_dump" else "sqlalchemy-jsonl",
    }
    path = dest / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _acquire_lock(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    lock = out / ".backup.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        age = time.time() - lock.stat().st_mtime
        if age > LOCK_STALE_SECONDS:
            lock.unlink(missing_ok=True)
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        else:
            raise RuntimeError(f"已有备份在跑（锁文件 {lock}，{age:.0f}s）")
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return lock


def prune(out: Path, keep: int) -> list[str]:
    """只删除形如 20261017-043018 的完整备份目录，按名称倒序保留 keep 份。"""
    if keep <= 0:
        return []
    candidates = sorted(
        (p for p in out.iterdir() if p.is_dir() and _looks_like_backup(p.name)),
        key=lambda p: p.name,
        reverse=True,
    )
    removed = []
    for old in candidates[keep:]:
        shutil.rmtree(old)
        removed.append(old.name)
    return removed


def _looks_like_backup(name: str) -> bool:
    try:
        datetime.strptime(name, TS_FORMAT)
        return True
    except ValueError:
        return False


def run_backup(database_url: str, out: Path, keep: int = DEFAULT_KEEP) -> tuple[Path, list[str]]:
    """执行一次备份，返回（产物目录，被清理的旧目录）。失败会抛异常。"""
    out = Path(out)
    lock = _acquire_lock(out)
    stamp = datetime.now().strftime(TS_FORMAT)
    partial = out / f"{stamp}.partial"
    final = out / stamp
    try:
        partial.mkdir(parents=True)
        if _is_postgres(database_url) and shutil.which("pg_dump"):
            backup_with_pg_dump(database_url, partial)
            write_manifest(partial, url=database_url, fmt="pg_dump", tables=None)
        else:
            counts = backup_with_sqlalchemy(database_url, partial)
            write_manifest(partial, url=database_url, fmt="jsonl", tables=counts)
        partial.rename(final)
    except Exception:
        # 半成品保留为 .partial，但不要挡住下一次备份
        raise
    finally:
        lock.unlink(missing_ok=True)
    return final, prune(out, keep)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AIOps 控制面数据库备份")
    parser.add_argument("--database-url", default="", help="默认取 .env 的 DATABASE_URL")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help=f"备份根目录（默认 {DEFAULT_OUT}）")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help=f"保留份数（默认 {DEFAULT_KEEP}）")
    args = parser.parse_args(argv)

    url = args.database_url
    if not url:
        from app.core.config import settings

        url = settings.database_url

    try:
        final, removed = run_backup(url, Path(args.out), keep=args.keep)
    except Exception as exc:  # noqa: BLE001
        print(f"[backup] 失败：{exc}", file=sys.stderr)
        return 1

    manifest = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
    total_rows = sum(manifest.get("tables", {}).values())
    size_mb = sum(f["bytes"] for f in manifest["files"].values()) / 1024 / 1024
    print(
        f"[backup] 完成 {final} | 格式 {manifest['tool']} | "
        f"表 {len(manifest.get('tables', {}))} | 行 {total_rows} | {size_mb:.1f} MB"
    )
    if removed:
        print(f"[backup] 已清理旧备份：{', '.join(removed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

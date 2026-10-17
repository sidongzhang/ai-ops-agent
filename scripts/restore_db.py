#!/usr/bin/env python3
"""从 `scripts/backup_db.py` 的产物恢复数据。

两种产物：
  - `dump.pgc` + `pg_restore` 可用 → 直接 `pg_restore --clean --if-exists`；
  - `tables/*.jsonl.gz` → 按外键依赖顺序 DELETE + INSERT（需要 `--yes`）。

用法：
  python scripts/restore_db.py --backup backups/20261017-043018            # 看计划
  python scripts/restore_db.py --backup backups/20261017-043018 --yes      # 实际执行
  python scripts/restore_db.py --backup ... --yes --database-url postgresql+psycopg2://...
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (REPO_ROOT, REPO_ROOT / "backend"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

BATCH = 500


def _adapt(value):
    """JSON/数组列在 JSONL 里是对象，psycopg2 不能直接适配 dict/list，先序列化成字符串。"""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _pg_conn_args(url: str) -> tuple[list[str], dict[str, str]]:
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


def _ordered_tables(conn) -> list[str]:
    """按外键依赖排序：被引用的表先恢复。周期/缺失引用时退化为字母序。"""
    from sqlalchemy import inspect

    inspector = inspect(conn)
    tables = sorted(inspector.get_table_names())
    deps: dict[str, set[str]] = {t: set() for t in tables}
    for table in tables:
        for fk in inspector.get_foreign_keys(table):
            ref = fk.get("referred_table")
            if ref and ref != table and ref in deps:
                deps[table].add(ref)

    ordered: list[str] = []
    while deps:
        ready = sorted(t for t, refs in deps.items() if not refs)
        if not ready:  # 环：按字母序打破
            ready = sorted(deps)[:1]
        for t in ready:
            ordered.append(t)
            deps.pop(t)
        for refs in deps.values():
            refs.difference_update(ready)
    return ordered


def _reset_sequences(conn, tables: list[str]) -> None:
    """恢复后把自增序列推到当前最大值之后（否则新写入会撞主键）。仅 PostgreSQL。"""
    if conn.dialect.name != "postgresql":
        return
    preparer = conn.dialect.identifier_preparer
    for table in tables:
        try:
            columns = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = :t "
                    "AND (column_default LIKE 'nextval%' OR is_identity = 'YES')"
                ),
                {"t": table},
            ).scalars().all()
            for column in columns:
                sequence = conn.execute(
                    text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": table, "c": column}
                ).scalar()
                if not sequence:
                    continue
                quoted_table = preparer.quote(table)
                quoted_column = preparer.quote(column)
                conn.execute(
                    text(
                        f"SELECT setval(:seq, COALESCE((SELECT MAX({quoted_column}) "
                        f"FROM {quoted_table}), 1))"
                    ),
                    {"seq": sequence},
                )
        except Exception:  # noqa: BLE001 - 单表序列修复失败不影响已恢复的数据
            continue


def _load_rows(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def restore_jsonl(backup_dir: Path, url: str, *, skip_missing: bool = False) -> dict[str, int]:
    from sqlalchemy import create_engine, inspect, text

    tables_dir = backup_dir / "tables"
    files = {p.name[: -len(".jsonl.gz")]: p for p in sorted(tables_dir.glob("*.jsonl.gz"))}
    if not files:
        raise RuntimeError(f"{tables_dir} 下没有可恢复的 *.jsonl.gz")

    engine = create_engine(url)
    counts: dict[str, int] = {}
    with engine.begin() as conn:
        existing = set(inspect(conn).get_table_names())
        missing = sorted(set(files) - existing)
        if missing and not skip_missing:
            raise RuntimeError(
                "目标库缺少这些表：" + ", ".join(missing) + "（可用 --skip-missing 跳过）"
            )
        # _ordered_tables 只返回目标库已存在的表；再按备份里实际有的表过滤
        order = [name for name in _ordered_tables(conn) if name in files]
        if missing:
            print(f"[restore] 跳过目标库不存在的表：{', '.join(missing)}", file=sys.stderr)
        # 先按反序清空（子表先删），再按正序插入（父表先插）
        for name in reversed(order):
            if name in files:
                quoted = conn.dialect.identifier_preparer.quote(name)
                conn.execute(text(f"DELETE FROM {quoted}"))

        for name in order:
            path = files.get(name)
            if not path:
                continue
            rows = _load_rows(path)
            counts[name] = len(rows)
            if not rows:
                continue
            quoted = conn.dialect.identifier_preparer.quote(name)
            cols = list(rows[0].keys())
            col_sql = ", ".join(conn.dialect.identifier_preparer.quote(c) for c in cols)
            bind_sql = ", ".join(f":p{i}" for i in range(len(cols)))
            stmt = text(f"INSERT INTO {quoted} ({col_sql}) VALUES ({bind_sql})")
            for start in range(0, len(rows), BATCH):
                chunk = [
                    {f"p{i}": _adapt(row.get(col)) for i, col in enumerate(cols)}
                    for row in rows[start : start + BATCH]
                ]
                conn.execute(stmt, chunk)

        _reset_sequences(conn, [name for name in order if name in counts])
    return counts


def restore_pg_dump(backup_dir: Path, url: str) -> None:
    if not shutil.which("pg_restore"):
        raise RuntimeError(
            "本机没有 pg_restore。请安装 PostgreSQL 客户端后重试："
            "brew install libpq && brew link --force libpq；"
            "或改用 JSONL 产物（备份时没有 pg_dump 会自动生成）。"
        )
    args, env = _pg_conn_args(url)
    cmd = [
        "pg_restore",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        *args,
        str(backup_dir / "dump.pgc"),
    ]
    subprocess.run(cmd, check=True, env=env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从备份恢复 AIOps 控制面数据库")
    parser.add_argument("--backup", required=True, help="backup_db.py 产物目录")
    parser.add_argument("--database-url", default="", help="默认取 .env 的 DATABASE_URL")
    parser.add_argument("--yes", action="store_true", help="确认执行（会删除现有数据）")
    parser.add_argument("--skip-missing", action="store_true", help="跳过目标库中不存在的表")
    args = parser.parse_args(argv)

    backup_dir = Path(args.backup)
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"[restore] {backup_dir} 不是有效备份目录（缺 manifest.json）", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    url = args.database_url
    if not url:
        from app.core.config import settings

        url = settings.database_url

    mode = "pg_restore" if (backup_dir / "dump.pgc").exists() else "jsonl"
    print(f"[restore] 备份 {backup_dir} | 创建于 {manifest.get('created_at')} | 目标 {manifest.get('database')} | 模式 {mode}")
    if not args.yes:
        print("[restore] 预览模式：加 --yes 才会真正写库（会清空同名表）。")
        return 0

    try:
        if mode == "pg_restore":
            restore_pg_dump(backup_dir, url)
            print("[restore] pg_restore 完成")
        else:
            counts = restore_jsonl(backup_dir, url, skip_missing=args.skip_missing)
            print(f"[restore] 完成，共 {len(counts)} 张表 / {sum(counts.values())} 行")
    except Exception as exc:  # noqa: BLE001
        print(f"[restore] 失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

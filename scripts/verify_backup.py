#!/usr/bin/env python3
"""备份恢复演练：把备份灌进同库的临时 schema、逐表对账，然后删除。

"备份成功"不等于"能恢复"。这里用 `CREATE TABLE ... (LIKE public.x INCLUDING ALL)`
复制**线上真实表结构**（包括 pgvector 列、checkpoint 表等非模型表），再灌 JSONL，
逐表核对行数——验证完 DROP SCHEMA，不碰生产数据。

用法：
  backend/.venv/bin/python scripts/verify_backup.py            # 用 backups/ 最新一份
  backend/.venv/bin/python scripts/verify_backup.py --backup backups/20261017-161050

限制：仅支持 PostgreSQL 与 JSONL 产物（pg_dump 产物请直接用 pg_restore 演练）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (REPO_ROOT, REPO_ROOT / "backend"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from scripts.restore_db import restore_jsonl  # noqa: E402


def _latest_backup(root: Path) -> Path | None:
    if not root.exists():
        return None
    candidates = sorted(
        p for p in root.iterdir() if p.is_dir() and len(p.name) == 15 and p.name[8] == "-"
    )
    return candidates[-1] if candidates else None


def _quote(conn, name: str) -> str:
    return conn.dialect.identifier_preparer.quote(name)


def verify(backup_dir: Path) -> int:
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"[verify] {backup_dir} 缺少 manifest.json", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "jsonl":
        print("[verify] 该备份是 pg_dump 产物，请用 pg_restore 演练", file=sys.stderr)
        return 1

    from app.core.config import settings

    url = make_url(settings.database_url)
    if not url.drivername.startswith("postgresql"):
        print("[verify] 仅支持 PostgreSQL", file=sys.stderr)
        return 1

    expected: dict[str, int] = {k: int(v) for k, v in manifest.get("tables", {}).items()}
    schema = f"restore_check_{int(time.time())}"
    base_url = url.render_as_string(hide_password=False)
    scratch_url = url.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(
        hide_password=False
    )

    engine = create_engine(base_url)
    print(f"[verify] 备份 {backup_dir.name} → 临时 schema {schema}")
    try:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            existing = inspect(conn).get_table_names(schema="public")
            for table in existing:
                conn.execute(
                    text(
                        f'CREATE TABLE "{schema}".{_quote(conn, table)} '
                        f"(LIKE public.{_quote(conn, table)} INCLUDING ALL)"
                    )
                )
            print(f"[verify] 已复制 {len(existing)} 张表结构")

        counts = restore_jsonl(backup_dir, scratch_url)

        mismatches = []
        with engine.connect() as conn:
            for table, rows in sorted(counts.items()):
                actual = conn.execute(
                    text(f'SELECT count(*) FROM "{schema}".{_quote(conn, table)}')
                ).scalar()
                if int(expected.get(table, -1)) != int(actual):
                    mismatches.append(f"{table}: 备份 {expected.get(table)} / 恢复 {actual}")

        skipped = sorted(set(expected) - set(counts))
        total_actual = sum(counts.values())
        total_expected = sum(expected[n] for n in counts if n in expected)
        print(f"[verify] 已恢复 {len(counts)} 张表 / {total_actual} 行（期望 {total_expected} 行）")
        if skipped:
            print(f"[verify] 备份中存在但未恢复：{', '.join(skipped)}")
        if mismatches:
            print("[verify] 行数不一致：", file=sys.stderr)
            for item in mismatches:
                print("  - " + item, file=sys.stderr)
            return 1
        if total_actual != total_expected:
            print("[verify] 总行数不一致", file=sys.stderr)
            return 1
        print("[verify] ✅ 恢复演练通过")
        return 0
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
        print(f"[verify] 已删除临时 schema {schema}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="备份恢复演练（临时 schema + 行数对账）")
    parser.add_argument("--backup", default="", help="备份目录；默认取 backups/ 最新一份")
    parser.add_argument("--out", default=str(REPO_ROOT / "backups"), help="备份根目录")
    args = parser.parse_args(argv)

    backup_dir = Path(args.backup) if args.backup else _latest_backup(Path(args.out))
    if not backup_dir:
        print("[verify] 找不到任何备份", file=sys.stderr)
        return 1
    try:
        return verify(backup_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"[verify] 失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

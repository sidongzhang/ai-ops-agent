"""备份/恢复脚本的回归测试（sqlite，不依赖 PostgreSQL 与 pg_dump）。

锁住的点：
1. 备份产物必须带 manifest（表行数 + 每个文件 sha256）；
2. 恢复后数据与备份前一致（按外键依赖顺序清空/插入）；
3. 保留策略只删完整备份目录，不碰 `.partial` 与其它文件；
4. 日志/清单里不得出现数据库密码。
"""
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from scripts.backup_db import _acquire_lock, mask_url, prune, run_backup
from scripts.restore_db import _ordered_tables, restore_jsonl


def _make_schema(db: Path) -> None:
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(
            text(
                "CREATE TABLE child (id INTEGER PRIMARY KEY, "
                "parent_id INTEGER REFERENCES parent(id), note TEXT)"
            )
        )


def _seed(db: Path) -> None:
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO parent (id, name) VALUES (1, 'a'), (2, 'b')"))
        conn.execute(
            text(
                "INSERT INTO child (id, parent_id, note) VALUES (10, 1, 'x'), (11, 2, 'y')"
            )
        )


def _rows(db: Path, table: str) -> list[tuple]:
    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(f"SELECT * FROM {table} ORDER BY id"))]


def test_backup_manifest_and_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "src.db"
    _make_schema(src)
    _seed(src)

    out = tmp_path / "backups"
    final, removed = run_backup(f"sqlite:///{src}", out, keep=3)
    assert removed == []

    manifest = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "jsonl"
    assert manifest["tables"] == {"child": 2, "parent": 2}
    assert "**" not in manifest["database"]  # 无密码可脱敏

    for rel, meta in manifest["files"].items():
        assert (final / rel).exists()
        assert len(meta["sha256"]) == 64

    dst = tmp_path / "dst.db"
    _make_schema(dst)
    counts = restore_jsonl(final, f"sqlite:///{dst}")

    assert counts == {"parent": 2, "child": 2}
    assert _rows(dst, "parent") == [(1, "a"), (2, "b")]
    assert _rows(dst, "child") == [(10, 1, "x"), (11, 2, "y")]


def test_restore_overwrites_existing_rows(tmp_path: Path) -> None:
    src = tmp_path / "src.db"
    _make_schema(src)
    _seed(src)
    out = tmp_path / "backups"
    final, _ = run_backup(f"sqlite:///{src}", out, keep=1)

    dst = tmp_path / "dst.db"
    _make_schema(dst)
    engine = create_engine(f"sqlite:///{dst}")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO parent (id, name) VALUES (99, 'stale')"))

    restore_jsonl(final, f"sqlite:///{dst}")
    assert _rows(dst, "parent") == [(1, "a"), (2, "b")]


def test_prune_keeps_newest_and_ignores_partial(tmp_path: Path) -> None:
    out = tmp_path / "backups"
    out.mkdir()
    for name in ("20200101-000000", "20200102-000000", "20200103-000000"):
        (out / name).mkdir()
    partial = out / "20200104-000000.partial"
    partial.mkdir()
    (out / "notes.txt").write_text("keep me", encoding="utf-8")

    removed = prune(out, keep=1)

    assert sorted(removed) == ["20200101-000000", "20200102-000000"]
    assert (out / "20200103-000000").is_dir()
    assert partial.is_dir()
    assert (out / "notes.txt").exists()


def test_lock_prevents_concurrent_backups(tmp_path: Path) -> None:
    out = tmp_path / "backups"
    lock = _acquire_lock(out)
    try:
        with pytest.raises(RuntimeError, match="已有备份在跑"):
            _acquire_lock(out)
    finally:
        lock.unlink()


def test_failed_backup_does_not_block_next_run(tmp_path: Path) -> None:
    out = tmp_path / "backups"
    with pytest.raises(Exception):
        run_backup("sqlite:////nonexistent-dir/nope.db", out, keep=1)
    # 锁必须释放，下一次仍可执行
    lock = _acquire_lock(out)
    lock.unlink()


def test_mask_url_hides_password() -> None:
    masked = mask_url("postgresql+psycopg2://ops:supersecret@127.0.0.1:5432/controlplane")
    assert "supersecret" not in masked
    assert masked.startswith("postgresql+psycopg2://ops:***@")


def test_foreign_key_order_puts_parent_first(tmp_path: Path) -> None:
    db = tmp_path / "g.db"
    _make_schema(db)
    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        assert _ordered_tables(conn).index("parent") < _ordered_tables(conn).index("child")

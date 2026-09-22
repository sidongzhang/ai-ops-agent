"""一次性迁移：SQLite(dev.db) → Postgres。

按 PG 元数据反射表结构，从 SQLite 读行，做类型适配后按依赖序插入，
最后重置自增序列。表存在两边才迁移；以 PG 为准（先清空）。

用法: cd backend && .venv/bin/python scripts/migrate_sqlite_to_pg.py [--truncate]
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import MetaData, create_engine, inspect, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402

SQLITE_PATH = Path(__file__).resolve().parent.parent / "dev.db"
SQLITE_URL = f"sqlite:///{SQLITE_PATH}"
PG_URL = settings.database_url

if PG_URL.startswith("sqlite"):
    print("错误：当前 DATABASE_URL 仍指向 SQLite，请先配置 Postgres。")
    sys.exit(1)

src_engine = create_engine(SQLITE_URL)
dst_engine = create_engine(PG_URL)

src_meta = MetaData()
src_meta.reflect(bind=src_engine)
dst_meta = MetaData()
dst_meta.reflect(bind=dst_engine)

common = [t for t in dst_meta.sorted_tables if t.name in src_meta.tables and t.name != "alembic_version"]
print(f"待迁移表（按依赖序）: {[t.name for t in common]}")


def adapt(col_type, value):
    if value is None:
        return None
    type_name = type(col_type).__name__.upper()
    if type_name == "BOOLEAN":
        return bool(value)
    if type_name in ("JSON", "JSONB"):
        return json.loads(value) if isinstance(value, str) else value
    if type_name in ("DATETIME", "TIMESTAMP") and isinstance(value, str):
        return datetime.fromisoformat(value)
    if type_name == "DATE" and isinstance(value, str):
        return date.fromisoformat(value)
    return value


if "--truncate" in sys.argv:
    with dst_engine.begin() as conn:
        for table in reversed(common):
            conn.execute(text(f'DELETE FROM "{table.name}"'))
    print("已清空 PG 目标表")

total = 0
with dst_engine.begin() as conn:
    for table in common:
        src_table = src_meta.tables[table.name]
        rows = []
        with src_engine.connect() as sconn:
            for row in sconn.execute(select(src_table)):
                rows.append({k: adapt(table.columns[k].type, v) for k, v in dict(row._mapping).items()
                             if k in table.columns})
        if rows:
            conn.execute(pg_insert(table).on_conflict_do_nothing(), rows)
        print(f"{table.name}: {len(rows)} 行")
        total += len(rows)

    # 重置自增序列，避免后续插入主键冲突
    for table in common:
        pk = "id"
        conn.execute(text(
            f"""SELECT setval(pg_get_serial_sequence('"{table.name}"', '{pk}'),
                 COALESCE((SELECT MAX(id) FROM "{table.name}"), 1))"""
        ))

print(f"完成，共迁移 {total} 行")

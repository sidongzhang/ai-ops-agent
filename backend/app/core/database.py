"""数据库引擎与会话。

统一使用 Postgres（deploy/docker-compose.yml 的 platform-db，含 pgvector），
启动时自动执行 alembic 迁移；未配置 DATABASE_URL 时回退 SQLite（无 Docker 兜底）。
"""
from pathlib import Path
from sqlalchemy import inspect
from sqlmodel import SQLModel, Session, create_engine

from .config import settings

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, echo=False, connect_args=_connect_args)

_SQLITE_REQUIRED_COLUMNS = {
    "systems": {
        "restart_policy": "JSON DEFAULT '{}'",
    },
    "action_workflows": {
        "requested_by_user_id": "INTEGER",
        "approved_by_user_id": "INTEGER",
        "target_service": "VARCHAR DEFAULT ''",
        "target_resource": "VARCHAR DEFAULT ''",
        "execution_mode": "VARCHAR DEFAULT ''",
        "approved_at": "DATETIME",
        "executed_at": "DATETIME",
    },
    "diagnosis_reports": {
        "knowledge_refs": "JSON DEFAULT '[]'",
        "feedback": "VARCHAR DEFAULT ''",
        "feedback_note": "VARCHAR DEFAULT ''",
        "feedback_at": "DATETIME",
    },
    "system_messages": {
        "incident_id": "INTEGER",
    },
}


def _ensure_sqlite_schema() -> None:
    with engine.begin() as conn:
        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())
        for table_name, columns in _SQLITE_REQUIRED_COLUMNS.items():
            if table_name not in table_names:
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, ddl in columns.items():
                if column_name in existing_columns:
                    continue
                conn.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")


def init_db():
    from app import models  # noqa: F401  # 注册所有模型到 metadata
    if settings.database_url.startswith("sqlite"):
        # SQLite 兜底（无 Docker 时的开发模式）：直接 create_all，无需 alembic
        SQLModel.metadata.create_all(engine)
        _ensure_sqlite_schema()
    else:
        # Postgres：启动时自动执行 alembic 迁移（幂等，已是 head 则无操作）
        _run_alembic_upgrade()
    from app.services.incidents.service import backfill_incidents

    with Session(engine) as session:
        backfill_incidents(session)


def _run_alembic_upgrade() -> None:
    from alembic import command
    from alembic.config import Config

    ini_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(ini_path))
    # alembic 默认会在 upgrade 时调用 fileConfig，而它以 disable_existing_loggers=True 行事，
    # 会把启动前已存在的 uvicorn / app.* logger **全部禁用** —— 表现为启动后应用日志（诊断耗时、
    # token、缓存命中、成本、工具链）与 uvicorn 访问日志一起消失。这里显式关闭日志重配。
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


def get_session():
    with Session(engine) as session:
        yield session

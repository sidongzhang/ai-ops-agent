"""数据库引擎与会话。

dev 默认 SQLite（零配置启动），生产设置 DATABASE_URL=postgresql+psycopg2://...
然后运行 alembic upgrade head 做 schema 迁移。
"""
from sqlmodel import SQLModel, Session, create_engine

from .config import settings

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, echo=False, connect_args=_connect_args)


def init_db():
    from app import models  # noqa: F401  # 注册所有模型到 metadata
    if settings.database_url.startswith("sqlite"):
        # dev 模式：直接 create_all，无需 alembic
        SQLModel.metadata.create_all(engine)
    # Postgres 生产模式：schema 由 `alembic upgrade head` 管理，此处不 create_all


def get_session():
    with Session(engine) as session:
        yield session

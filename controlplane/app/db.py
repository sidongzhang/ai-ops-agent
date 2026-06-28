"""数据库引擎与会话。dev 用 SQLite，生产换 DATABASE_URL 即可。"""
from sqlmodel import SQLModel, Session, create_engine

from .config import settings

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, echo=False, connect_args=_connect_args)


def init_db():
    # 导入模型以注册到 metadata
    from . import models  # noqa: F401
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session

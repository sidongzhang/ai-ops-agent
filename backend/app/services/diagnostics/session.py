"""诊断子系统的统一数据库会话入口（可注入，便于单测用临时 SQLite）。

cancel_bus / reaper / usage / shutdown 都从这里取 session，避免各自复制一份注入逻辑，
也避免单测不小心打到开发库。
"""
from typing import Callable

from sqlmodel import Session

_session_factory: Callable[[], Session] | None = None


def set_session_factory(factory: Callable[[], Session] | None) -> None:
    global _session_factory
    _session_factory = factory


def session() -> Session:
    if _session_factory is not None:
        return _session_factory()
    from app.core.database import engine

    return Session(engine)


def dialect_name(session_obj: Session) -> str:
    try:
        return session_obj.get_bind().dialect.name
    except Exception:  # noqa: BLE001
        return "postgresql"


def row_lock_clause(session_obj: Session) -> str:
    """Postgres 才支持 SELECT … FOR UPDATE；SQLite 单写者语义下不需要。"""
    return " FOR UPDATE" if dialect_name(session_obj) == "postgresql" else ""


__all__ = ["session", "set_session_factory", "dialect_name", "row_lock_clause"]

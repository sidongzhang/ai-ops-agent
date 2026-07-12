"""Persistence helpers for collectors."""
from sqlmodel import Session, select

from ..models.collectors import Collector


def list_collectors_for_system(session: Session, system_id: int) -> list[Collector]:
    return list(session.exec(select(Collector).where(Collector.system_id == system_id)))


def get_first_collector_for_system(session: Session, system_id: int) -> Collector | None:
    """Return the best available collector for legacy callers.

    A system may have more than one collector.  Ordering by the latest
    report keeps an old, disconnected collector from masking a healthy one.
    The function name is kept for compatibility with existing service code.
    """
    return session.exec(
        select(Collector)
        .where(Collector.system_id == system_id)
        .order_by(Collector.last_seen.desc(), Collector.id.desc())
    ).first()

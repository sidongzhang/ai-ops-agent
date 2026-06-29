"""Persistence helpers for collectors."""
from sqlmodel import Session, select

from ..models.collectors import Collector


def list_collectors_for_system(session: Session, system_id: int) -> list[Collector]:
    return list(session.exec(select(Collector).where(Collector.system_id == system_id)))


def get_first_collector_for_system(session: Session, system_id: int) -> Collector | None:
    return session.exec(select(Collector).where(Collector.system_id == system_id)).first()

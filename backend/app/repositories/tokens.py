"""Persistence helpers for external system tokens."""
from sqlmodel import Session, select

from app.models.tokens import SystemToken


def list_tokens_for_system(session: Session, system_id: int) -> list[SystemToken]:
    return list(
        session.exec(
            select(SystemToken)
            .where(SystemToken.system_id == system_id)
            .order_by(SystemToken.created_at.desc())
        )
    )


def get_token_for_system(session: Session, system_id: int, token_id: int) -> SystemToken | None:
    token = session.get(SystemToken, token_id)
    if not token or token.system_id != system_id:
        return None
    return token


def get_token_by_hash(session: Session, token_hash: str) -> SystemToken | None:
    return session.exec(
        select(SystemToken).where(SystemToken.token_hash == token_hash)
    ).first()

"""Business logic for external system token management."""
from sqlmodel import Session

from app.core.security import generate_system_token, hash_system_token
from app.models.tokens import SystemToken
from app.repositories.tokens import get_token_for_system, list_tokens_for_system
from app.schemas.tokens import SystemTokenCreate, SystemTokenCreated, SystemTokenOut
from app.services.systems.service import require_system


def create_system_token(
    session: Session,
    system_id: int,
    org_id: int,
    body: SystemTokenCreate,
) -> SystemTokenCreated:
    system = require_system(session, system_id, org_id)
    raw_token = generate_system_token()
    token = SystemToken(
        org_id=org_id,
        system_id=system.id,
        name=body.name,
        token_hash=hash_system_token(raw_token),
        scopes=body.scopes,
        allowed_ips=body.allowed_ips,
        expires_at=body.expires_at,
    )
    session.add(token)
    session.commit()
    session.refresh(token)
    return SystemTokenCreated(
        id=token.id,
        name=token.name,
        system_id=system.id,
        token=raw_token,
        scopes=token.scopes,
        allowed_ips=token.allowed_ips,
        expires_at=token.expires_at,
    )


def list_system_tokens(session: Session, system_id: int, org_id: int) -> list[SystemTokenOut]:
    system = require_system(session, system_id, org_id)
    return [_to_out(token) for token in list_tokens_for_system(session, system.id)]


def revoke_system_token(session: Session, system_id: int, token_id: int, org_id: int) -> SystemTokenOut:
    system = require_system(session, system_id, org_id)
    token = get_token_for_system(session, system.id, token_id)
    if not token:
        raise LookupError("Token 不存在")
    token.status = "revoked"
    session.add(token)
    session.commit()
    session.refresh(token)
    return _to_out(token)


def _to_out(token: SystemToken) -> SystemTokenOut:
    return SystemTokenOut(
        id=token.id,
        name=token.name,
        system_id=token.system_id,
        scopes=token.scopes,
        allowed_ips=token.allowed_ips,
        status=token.status,
        expires_at=token.expires_at,
        last_used_at=token.last_used_at,
        created_at=token.created_at,
    )

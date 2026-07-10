"""External system token management API."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.core.database import get_session
from app.core.deps import get_current_org_id
from app.schemas.tokens import SystemTokenCreate, SystemTokenCreated, SystemTokenOut
from app.services.tokens import create_system_token, list_system_tokens, revoke_system_token

router = APIRouter(prefix="/systems/{system_id}/tokens", tags=["system-tokens"])


@router.post("", response_model=SystemTokenCreated, status_code=status.HTTP_201_CREATED)
def create_token(
    system_id: int,
    body: SystemTokenCreate,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    try:
        return create_system_token(session, system_id, org_id, body)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("", response_model=list[SystemTokenOut])
def list_tokens(
    system_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    try:
        return list_system_tokens(session, system_id, org_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{token_id}/revoke", response_model=SystemTokenOut)
def revoke_token(
    system_id: int,
    token_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    try:
        return revoke_system_token(session, system_id, token_id, org_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))

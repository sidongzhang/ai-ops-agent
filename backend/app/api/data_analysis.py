"""Read-only business data analysis endpoint."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.core.database import get_session
from app.core.deps import get_current_org_id, get_current_user
from app.models.auth import User
from app.schemas import DataAnalysisRequest, DataAnalysisResponse, ReadonlyDatabaseConfig
from app.services.data_analysis import (
    analyze_system_data,
    get_readonly_database_config,
    test_readonly_database_config,
    update_readonly_database_config,
)

router = APIRouter(prefix="/systems", tags=["data-analysis"])


@router.get("/{system_id}/readonly-database", response_model=ReadonlyDatabaseConfig)
def get_readonly_database(
    system_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    try:
        return get_readonly_database_config(session, system_id, org_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.put("/{system_id}/readonly-database", response_model=ReadonlyDatabaseConfig)
def update_readonly_database(
    system_id: int,
    body: ReadonlyDatabaseConfig,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    try:
        return update_readonly_database_config(session, system_id, org_id, body)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.post("/{system_id}/readonly-database/test", response_model=dict)
def test_readonly_database(
    system_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    try:
        return test_readonly_database_config(session, system_id, org_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.post("/{system_id}/data-analysis", response_model=DataAnalysisResponse)
def analyze_data(
    system_id: int,
    body: DataAnalysisRequest,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user: User = Depends(get_current_user),
):
    try:
        return analyze_system_data(
            session,
            system_id,
            org_id,
            body.question,
            actor_type="user",
            actor_id=str(user.id),
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

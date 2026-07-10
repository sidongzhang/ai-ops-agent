"""Management-facing operational efficiency analytics API."""
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.core.database import get_session
from app.core.deps import get_current_org_id
from app.schemas.analytics import EfficiencyAnalyticsOut
from app.services.analytics import get_efficiency_analytics

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/efficiency", response_model=EfficiencyAnalyticsOut)
def efficiency_analytics(
    days: int = Query(default=30, ge=1, le=90),
    system_id: int | None = None,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    return get_efficiency_analytics(
        session,
        org_id,
        days=days,
        system_id=system_id,
    )

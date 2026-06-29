"""System metrics API."""
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from ..core.database import get_session
from ..core.deps import get_current_org_id
from ..services.monitoring.metrics import SystemMetrics, get_metrics as get_system_metrics

router = APIRouter(prefix="/systems", tags=["metrics"])


@router.get("/{system_id}/metrics", response_model=SystemMetrics)
def get_metrics(
    system_id: int,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    try:
        return get_system_metrics(session, system_id, org_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))

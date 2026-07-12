"""System metrics API."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..core.database import get_session
from ..core.deps import get_current_org_id
from ..services.monitoring.metrics import MetricsHistoryOut, SystemMetrics, get_metrics as get_system_metrics
from ..services.monitoring.metrics import get_metrics_history

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


@router.get("/{system_id}/metrics/history", response_model=MetricsHistoryOut)
def get_metrics_history_endpoint(
    system_id: int,
    range: str = Query(default="1h", pattern="^(1h|6h|24h)$"),
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
):
    try:
        return get_metrics_history(session, system_id, org_id, range_key=range)
    except LookupError as exc:
        raise HTTPException(404, str(exc))

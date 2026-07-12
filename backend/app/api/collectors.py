"""采集器管理（用户侧，JWT）。为某套系统创建/列出采集器。"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlmodel import Session

from ..core.database import get_session
from ..core.deps import get_current_org_id, require_operator
from ..schemas import CollectorBundleRequest, CollectorCreate, CollectorCreated, CollectorOut
from ..services.collectors.service import (
    create_collector as create_collector_record,
    delete_collector as delete_collector_record,
    list_collectors as list_collector_records,
    build_collector_bundle,
)

router = APIRouter(prefix="/systems/{system_id}/collectors", tags=["collectors"])


@router.post("", response_model=CollectorCreated, status_code=status.HTTP_201_CREATED)
def create_collector(system_id: int, body: CollectorCreate,
                     session: Session = Depends(get_session),
                     org_id: int = Depends(get_current_org_id),
                     user = Depends(require_operator)):
    try:
        return create_collector_record(session, system_id, org_id, body)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("", response_model=list[CollectorOut])
def list_collectors(system_id: int, session: Session = Depends(get_session),
                    org_id: int = Depends(get_current_org_id)):
    try:
        return list_collector_records(session, system_id, org_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.delete("/{collector_id}")
def delete_collector(system_id: int, collector_id: int,
                     session: Session = Depends(get_session),
                     org_id: int = Depends(get_current_org_id),
                     user = Depends(require_operator)):
    delete_collector_record(session, collector_id, org_id, system_id=system_id)
    return {"ok": True}


@router.post("/{collector_id}/bundle")
def download_collector_bundle(
    system_id: int,
    collector_id: int,
    body: CollectorBundleRequest,
    request: Request,
    session: Session = Depends(get_session),
    org_id: int = Depends(get_current_org_id),
    user = Depends(require_operator),
):
    try:
        bundle = build_collector_bundle(
            session,
            system_id,
            collector_id,
            org_id,
            body.collector_key,
            str(request.base_url),
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(403, str(exc))
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))
    return Response(
        content=bundle,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="collector-{collector_id}.zip"'},
    )

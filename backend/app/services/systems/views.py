"""读模型序列化（最底层，无内部依赖）。"""
from app.core.security import mask_sensitive_fields
from app.models.systems import Service
from app.schemas import ServiceOut


def service_out(service: Service) -> ServiceOut:
    return ServiceOut(
        id=service.id,
        name=service.name,
        connector=service.connector,
        config=mask_sensitive_fields(service.config),
        enabled=service.enabled,
        probe_status=service.probe_status,
        probe_detail=service.probe_detail,
        tested_at=service.tested_at,
    )

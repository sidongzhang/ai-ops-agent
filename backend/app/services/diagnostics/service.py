"""Application service for AI diagnosis requests."""
from sqlmodel import Session

from app.agent.diagnostics.runner import diagnose
from app.repositories.systems import list_services_for_system
from app.schemas import DiagnoseResponse
from app.services.descriptors.builder import system_to_descriptor
from app.services.systems.service import require_system


def diagnose_system(session: Session, system_id: int, org_id: int, question: str) -> DiagnoseResponse:
    system = require_system(session, system_id, org_id)
    descriptor = system_to_descriptor(system, list_services_for_system(session, system.id))
    answer = diagnose(descriptor, question, org_id=org_id, system_id=system.id)
    return DiagnoseResponse(system_id=system.id, answer=answer)

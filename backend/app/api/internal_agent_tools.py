"""Per-run capability endpoint; intentionally excluded from public OpenAPI docs."""
import json
import asyncio

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agent.harness.bridge import authorized_binding, invoke_bound
from app.agent.harness.gateway import ToolAuditError, ToolCancelled

router = APIRouter(prefix='/internal/agent-tools', include_in_schema=False)


class ToolCall(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    call_id: str = Field(min_length=1, max_length=200, pattern=r'^[a-zA-Z0-9_\-:.]+$')
    tool: str = Field(min_length=1, max_length=80)
    arguments: dict = Field(default_factory=dict)


@router.post('/runs/{run_id}/calls')
async def call_tool(run_id: str, body: ToolCall, authorization: str = Header(default='')):
    bearer = authorization.removeprefix('Bearer ') if authorization.startswith('Bearer ') else ''
    if len(json.dumps(body.arguments).encode()) > 64 * 1024:
        raise HTTPException(413, 'arguments_too_large')
    binding = authorized_binding(run_id, bearer)
    if binding is None:
        from app.agent.harness.bridge_rpc import forward
        status, payload = await asyncio.to_thread(forward, run_id, bearer, "call", body.model_dump())
        return JSONResponse(payload, status_code=status)
    try:
        status, payload = await invoke_bound(binding, body.call_id, body.tool, body.arguments)
    except ToolCancelled:
        raise HTTPException(409, 'run_cancelled')
    except ToolAuditError:
        raise HTTPException(503, 'tool_audit_unavailable')
    return JSONResponse(payload, status_code=status)


@router.get('/runs/{run_id}/status')
async def run_execution_status(run_id: str, authorization: str = Header(default='')):
    binding = authorized_binding(run_id, authorization.removeprefix('Bearer '))
    if binding is None:
        from app.agent.harness.bridge_rpc import forward
        status, payload = await asyncio.to_thread(forward, run_id, authorization.removeprefix('Bearer '), "status")
        return JSONResponse(payload, status_code=status)
    from app.agent.harness.bridge_rpc import binding_status
    return binding_status(binding, run_id)

"""Compatibility bridge used by platform services; no native SDK objects required."""
from .contracts import RuntimeCheckpoint, RuntimeRequest
from .factory import get_runtime


def diagnose_with_details(
    descriptor, question, org_id=0, system_id=0, *,
    runtime_engine="pydantic", runtime_engine_version="", history_schema_version=1,
    on_runtime_event=None, runtime_run_id="", **options,
):
    checkpoint = None
    history = options.get("message_history")
    if isinstance(history, str) and history:
        checkpoint = RuntimeCheckpoint(
            history, options.get("prior_usage") or "", runtime_engine,
            runtime_engine_version, history_schema_version,
        )
    return get_runtime(runtime_engine).execute(RuntimeRequest(
        descriptor=descriptor, question=question, org_id=org_id, system_id=system_id,
        run_id=runtime_run_id, options=options, checkpoint=checkpoint, on_event=on_runtime_event,
    ))


def diagnose(descriptor, question, org_id=0, system_id=0, skill_steps=None):
    return diagnose_with_details(
        descriptor, question, org_id=org_id, system_id=system_id, skill_steps=skill_steps,
    ).answer

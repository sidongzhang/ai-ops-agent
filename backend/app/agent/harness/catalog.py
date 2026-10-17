"""Single tool directory; implementations are not registered against an engine here."""
from dataclasses import dataclass
from functools import lru_cache
import inspect
from typing import Callable

from pydantic import ConfigDict, create_model

EVIDENCE_TOOLS = frozenset(('list_services', 'check_service', 'check_container_state', 'read_logs', 'search_logs', 'query_prometheus', 'run_kafka_command', 'run_redis_command'))
TOOL_CLASSES = {
    **dict.fromkeys(('list_services', 'check_service', 'check_container_state', 'read_logs', 'search_logs'), 'health'),
    **dict.fromkeys(('query_prometheus', 'run_kafka_command', 'run_redis_command', 'investigate', 'consult_specialists', 'mcp_call'), 'metrics'),
    **dict.fromkeys(('query_business_data', 'query_business_dataset', 'list_archive_files', 'read_archive_file', 'fetch_archive_file', 'run_python_analysis'), 'business'),
    **dict.fromkeys(('get_service_topology', 'analyze_root_cause', 'recent_changes', 'search_knowledge_base', 'load_runbook', 'mcp_list_tools'), 'static'),
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    implementation: Callable
    input_model: type
    risk: str
    timeout_seconds: float
    freshness: str
    cancellation: str
    retryable: bool = False
    replayable: bool = False  # read-only alone is not an exactly-once guarantee
    evidence_only: bool = False
    output_semantics: str = 'Text evidence or explicit failure; no verified conclusion implied'

    def describe(self):
        return {key: getattr(self, key) for key in ('name', 'risk', 'timeout_seconds', 'freshness', 'cancellation', 'retryable', 'replayable', 'evidence_only', 'output_semantics')} | {
            'input_schema': self.input_model.model_json_schema(),
            'description': inspect.getdoc(self.implementation) or '',
        }


@lru_cache(maxsize=1)
def tool_catalog() -> tuple[ToolSpec, ...]:
    from ..diagnostics import tools

    specs = []
    for name, freshness in TOOL_CLASSES.items():
        implementation = getattr(tools, name)
        fields = {p.name: (p.annotation, ... if p.default is inspect.Parameter.empty else p.default)
                  for p in inspect.signature(implementation).parameters.values() if p.name != 'ctx'}
        model = create_model(f'{name}Input', __config__=ConfigDict(extra='forbid', strict=True), **fields)
        delegation = name in ('investigate', 'consult_specialists')
        risk = {'fetch_archive_file': 'export', 'run_python_analysis': 'sandbox', 'mcp_call': 'external_read'}.get(name, 'delegation' if delegation else 'read')
        specs.append(ToolSpec(name, implementation, model, risk, 300 if delegation or risk == 'sandbox' else 60,
                              freshness, 'task' if inspect.iscoroutinefunction(implementation) else 'stop_waiting_only',
                              evidence_only=name in EVIDENCE_TOOLS))
    return tuple(specs)

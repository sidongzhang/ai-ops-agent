"""Host-side contracts: no model SDK types, clients, or credentials at import time.

Options may contain trusted Python callbacks while the existing engine is in-process.
They are not a network payload; a future Pi adapter must serialize references instead.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

HISTORY_SCHEMA_VERSION = 1


class RuntimeCompatibilityError(ValueError):
    """Unknown engine or history format; never retry with a different engine."""


class RuntimeExecutionError(RuntimeError):
    """Selected runtime failed; never silently switch engines."""

    def __init__(self, message, partial_result=None):
        super().__init__(message)
        self.partial_result = partial_result


class RuntimeBudgetExceeded(RuntimeExecutionError):
    """Engine usage limit reached; the platform must not start a fallback run."""


class CancellationHandle(Protocol):
    def cancel(self) -> None: ...


@dataclass(frozen=True)
class RuntimeCheckpoint:
    messages_json: str
    usage_json: str = ""
    engine: str = "pydantic"
    engine_version: str = ""  # blank denotes existing, pre-versioned Pydantic history
    history_schema_version: int = HISTORY_SCHEMA_VERSION


@dataclass(frozen=True)
class RuntimeCapabilities:
    engine: str
    engine_version: str
    history_schema_version: int = HISTORY_SCHEMA_VERSION
    images: bool = True  # requires a compatible selected model
    subagents: bool = True
    cancellation: bool = True
    resume: bool = True


@dataclass(frozen=True)
class UsageBaseline:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    requests: int = 0
    tool_calls: int = 0


@dataclass(frozen=True)
class RuntimeEvent:
    run_id: str
    sequence: int
    kind: str
    timestamp: str
    payload: dict = field(default_factory=dict)
    schema_version: int = 1


@dataclass
class RuntimeRequest:
    descriptor: dict
    question: str
    org_id: int = 0
    system_id: int = 0
    run_id: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    checkpoint: RuntimeCheckpoint | None = None
    on_event: Callable[[RuntimeEvent], None] | None = None


@dataclass
class RuntimeResult:
    answer: str
    model: str
    duration_ms: int
    total_tokens: int = 0
    tool_calls: list[dict] = field(default_factory=list)
    cancelled: bool = False
    messages_json: str = ""
    conversation_id: str = ""
    run_id: str = ""
    usage_json: str = ""
    engine: str = "pydantic"
    engine_version: str = ""
    history_schema_version: int = HISTORY_SCHEMA_VERSION
    resume_safe: bool = True


class AgentRuntime(Protocol):
    """Blocking execution bridge for existing BackgroundTasks, not an admission API.

    Report admission, distributed ownership, event persistence and settlement still
    belong to the platform. Do not pretend an in-memory adapter is durable execution.
    """
    def capabilities(self) -> RuntimeCapabilities: ...
    def create_cancellation(self) -> CancellationHandle: ...
    def validate_checkpoint(self, checkpoint: RuntimeCheckpoint) -> None: ...
    def validate_resume_checkpoint(self, checkpoint: RuntimeCheckpoint) -> None: ...
    def prepare_resume(self, checkpoint: RuntimeCheckpoint, *, force_refresh: bool, ttl_for_tool: Callable[[str], int]) -> tuple[str, list[dict]]: ...
    def execute(self, request: RuntimeRequest) -> RuntimeResult: ...

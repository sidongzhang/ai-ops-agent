"""Framework-neutral runtime boundary. Engine implementations load on demand."""
from .contracts import RuntimeCheckpoint, RuntimeEvent, RuntimeRequest, RuntimeResult, RuntimeBudgetExceeded
from .factory import get_runtime

__all__ = ["RuntimeCheckpoint", "RuntimeEvent", "RuntimeRequest", "RuntimeResult", "RuntimeBudgetExceeded", "get_runtime"]

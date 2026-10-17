"""Explicit selection: Pydantic primary, Pi available only behind its trial flag."""
from .contracts import AgentRuntime, RuntimeCompatibilityError


def get_runtime(engine: str = "pydantic") -> AgentRuntime:
    if engine == "pi":
        from app.core.config import settings
        if not settings.pi_runtime_enabled:
            raise RuntimeCompatibilityError("Pi试点未启用")
        from .pi_runtime import PiRuntime
        return PiRuntime()
    if engine != "pydantic":
        raise RuntimeCompatibilityError(f"运行引擎 {engine!r} 尚未接入，不能切换或跨引擎恢复")
    from .pydantic_runtime import PydanticRuntime

    return PydanticRuntime()

"""Optional Langfuse tracing for diagnosis runs."""
import logging

from ...core.config import settings

log = logging.getLogger(__name__)


def get_langfuse():
    if not settings.langfuse_public_key:
        return None
    try:
        from langfuse import Langfuse

        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except Exception as exc:
        log.warning(f"[langfuse] 初始化失败（跳过追踪）: {exc}")
        return None

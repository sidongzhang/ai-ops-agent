"""One age policy for history projections and bounded evidence checks."""
from app.core.config import settings
from .catalog import TOOL_CLASSES


def ttl_seconds(tool):
    return max(0, int((settings.evidence_stale_ttl or {}).get(TOOL_CLASSES.get(tool, 'metrics'), 300)))

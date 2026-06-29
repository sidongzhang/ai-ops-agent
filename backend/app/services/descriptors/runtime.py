"""Connector runtime wiring."""
import os
import sys

from ...core.config import settings

_shared = os.path.join(settings.repo_root, "shared")
if _shared not in sys.path:
    sys.path.insert(0, _shared)

from connectors import get_connector  # noqa: E402

__all__ = ["get_connector"]

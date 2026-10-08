"""Connector runtime wiring."""
from ...core.shared_path import ensure_shared_path

ensure_shared_path()

from connectors import get_connector  # noqa: E402

__all__ = ["get_connector"]

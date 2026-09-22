"""Service-layer convenience exports.

Keep this package lightweight: importing every service eagerly creates circular
imports with the diagnosis agent, because tools depend on descriptor services
while diagnostics depend on the agent runner.
"""
from importlib import import_module

_EXPORTS = {
    "create_collector": ("app.services.collectors", "create_collector"),
    "list_collectors": ("app.services.collectors", "list_collectors"),
    "get_collector_config": ("app.services.collectors", "get_collector_config"),
    "record_collector_report": ("app.services.collectors", "record_collector_report"),
    "exec_collector": ("app.services.collectors", "exec_collector"),
    "diagnose_system": ("app.services.diagnostics", "diagnose_system"),
    "get_system_health": ("app.services.monitoring", "get_system_health"),
    "get_metrics_snapshot": ("app.services.monitoring", "get_metrics_snapshot"),
    "monitor_all_systems": ("app.services.monitoring", "monitor_all_systems"),
    "send_alert_for_system": ("app.services.notifications", "send_alert_for_system"),
    "send_test_notification": ("app.services.notifications", "send_test_notification"),
    "merge_notify_config": ("app.services.notifications", "merge_notify_config"),
    "create_system": ("app.services.systems", "create_system"),
    "list_systems": ("app.services.systems", "list_systems"),
    "get_system": ("app.services.systems", "get_system"),
    "add_service": ("app.services.systems", "add_service"),
    "delete_service": ("app.services.systems", "delete_service"),
    "update_notify": ("app.services.systems", "update_notify"),
    "get_decrypted_notify": ("app.services.systems", "get_decrypted_notify"),
    "start_workflow": ("app.services.workflows", "start_workflow"),
    "list_workflows": ("app.services.workflows", "list_workflows"),
    "get_workflow": ("app.services.workflows", "get_workflow"),
    "approve_workflow": ("app.services.workflows", "approve_workflow"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value

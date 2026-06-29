from . import (
    auth,
    collector_exec,
    collector_gateway,
    collectors,
    diagnose,
    feishu_webhook,
    health,
    metrics,
    systems,
    websocket,
    workflow,
)

ROUTERS = (
    auth.router,
    systems.router,
    health.router,
    diagnose.router,
    collectors.router,
    collector_gateway.router,
    collector_exec.router,
    metrics.router,
    workflow.router,
    websocket.router,
    feishu_webhook.router,
)

__all__ = [
    "ROUTERS",
    "auth",
    "systems",
    "health",
    "diagnose",
    "collectors",
    "collector_gateway",
    "collector_exec",
    "metrics",
    "workflow",
    "websocket",
    "feishu_webhook",
]

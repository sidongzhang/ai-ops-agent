"""Operation action catalog used by AI repair workflows.

The catalog is intentionally small and product-facing: every executable action
has a clear label, risk level, approval rule, and recovery check. AI proposals
are normalized through this layer before users approve them.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass


@dataclass(frozen=True)
class ActionSpec:
    action_type: str
    label: str
    description: str
    risk: str
    approval_required: bool
    verification: str
    allowed_remote: bool = True


ACTION_CATALOG: dict[str, ActionSpec] = {
    "health_check": ActionSpec(
        action_type="health_check",
        label="健康检查",
        description="重新检查已注册服务的运行状态。",
        risk="low",
        approval_required=False,
        verification="读取健康检查结果",
    ),
    "fetch_logs": ActionSpec(
        action_type="fetch_logs",
        label="拉取日志",
        description="读取指定服务的近期日志，用于定位错误。",
        risk="low",
        approval_required=False,
        verification="确认日志已成功返回",
    ),
    "restart_container": ActionSpec(
        action_type="restart_container",
        label="重启容器",
        description="重启已登记的 Docker 容器。",
        risk="medium",
        approval_required=True,
        verification="执行后重新检查服务健康状态",
    ),
    "restart_systemd": ActionSpec(
        action_type="restart_systemd",
        label="重启 systemd 服务",
        description="重启已登记的 systemd 服务。",
        risk="medium",
        approval_required=True,
        verification="执行后重新检查服务健康状态",
    ),
    "run_redis_command": ActionSpec(
        action_type="run_redis_command",
        label="Redis 运维命令",
        description="执行受控 Redis 运维命令。",
        risk="high",
        approval_required=True,
        verification="记录命令返回结果，必要时人工确认数据影响",
        allowed_remote=False,
    ),
    "manual": ActionSpec(
        action_type="manual",
        label="人工处理",
        description="平台只给出处理步骤，由人工确认后执行。",
        risk="manual",
        approval_required=False,
        verification="人工确认处理结果",
    ),
}


def list_action_catalog() -> list[dict]:
    return [action_spec_to_dict(spec) for spec in ACTION_CATALOG.values()]


def action_spec_to_dict(spec: ActionSpec) -> dict:
    return {
        "type": spec.action_type,
        "label": spec.label,
        "description": spec.description,
        "risk": spec.risk,
        "approval_required": spec.approval_required,
        "verification": spec.verification,
        "allowed_remote": spec.allowed_remote,
    }


def normalize_action(action: dict | None, *, descriptor: dict | None = None) -> dict:
    """Return a safe, displayable action payload.

    Unknown or unavailable actions become manual steps instead of falling
    through to raw AI output.
    """
    raw = deepcopy(action or {})
    action_type = str(raw.get("type") or "manual")
    spec = ACTION_CATALOG.get(action_type)
    if spec is None:
        return normalize_action(
            {
                "type": "manual",
                "description": f"AI 提出了暂不支持的操作「{action_type}」，请人工评估后处理。",
                "service": raw.get("service", ""),
                "manual_steps": raw.get("manual_steps") or ["查看诊断结论", "人工确认处理方式", "记录处理结果"],
            },
            descriptor=descriptor,
        )

    descriptor = descriptor or {}
    is_remote = not bool(descriptor.get("local"))
    if is_remote and not spec.allowed_remote:
        return normalize_action(
            {
                "type": "manual",
                "description": f"{spec.label} 暂不允许在远程系统自动执行，请人工确认。",
                "service": raw.get("service", ""),
                "manual_steps": [
                    "确认远程系统负责人已授权",
                    "在对方环境手动执行必要命令",
                    "回到平台查看服务健康和日志是否恢复",
                ],
            },
            descriptor=descriptor,
        )

    raw["type"] = spec.action_type
    raw.setdefault("description", spec.description)
    raw["catalog"] = action_spec_to_dict(spec)
    raw["risk"] = spec.risk
    raw["approval_required"] = spec.approval_required
    raw["verification"] = spec.verification
    raw["display_name"] = spec.label
    return raw

"""只读操作策略：Redis / Kafka 命令白名单的**唯一实现**。

被三处共用（此前各自维护一份，语义已经跑偏）：
  * 诊断 Agent 工具     backend/app/agent/diagnostics/tools.py
  * MCP 运维工具服务    backend/app/agent/diagnostics/mcp_server.py
  * 采集器 WS 下行通道  collector/ws_client.py

历史问题（本次重构修复）：
  Agent 工具版只校验动词，`CONFIG SET maxmemory 1mb` 这类**写操作可绕过白名单**；
  MCP 版动词集合与采集器版不同；三处行为不一致，改一处漏两处就是安全漏洞。

统一规则：动词白名单 + 需要子命令的动词再做「只读子命令」约束；
拒绝时返回中文原因，放行返回 None。
"""
import shlex

# ── Redis ──────────────────────────────────────────────────────────
REDIS_READONLY_VERBS = frozenset({
    "INFO", "DBSIZE", "PING", "KEYS", "SCAN", "TTL", "TYPE",
    "LLEN", "SCARD", "ZCARD", "HLEN", "STRLEN", "GET",
    "CLIENT", "CONFIG", "SLOWLOG", "MEMORY", "OBJECT",
})

# 需要子命令约束的动词 → 只读子命令集合
REDIS_READONLY_SUBCOMMANDS = {
    "CLIENT": frozenset({"LIST", "INFO", "GETNAME", "ID"}),
    "CONFIG": frozenset({"GET"}),
    "SLOWLOG": frozenset({"GET", "LEN"}),
    "MEMORY": frozenset({"USAGE", "STATS", "DOCTOR"}),
    "OBJECT": frozenset({"ENCODING", "FREQ", "IDLETIME", "REFCOUNT"}),
}

# ── Kafka ──────────────────────────────────────────────────────────
KAFKA_READONLY_SUBCOMMANDS = {
    "topics": frozenset({"--list", "--describe"}),
    "consumer-groups": frozenset({"--describe", "--list", "--state"}),
}

KAFKA_DANGEROUS_FLAGS = frozenset({
    "--create", "--delete", "--alter", "--reset-offsets", "--execute",
    "--command-config", "--zookeeper",
})


def redis_command_denied(command: str) -> str | None:
    """Redis 命令是否被拒绝：返回拒绝原因，None 表示放行。"""
    parts = (command or "").split()
    if not parts:
        return "安全限制：Redis 命令为空"
    verb = parts[0].upper()
    if verb not in REDIS_READONLY_VERBS:
        return f"安全限制：拒绝执行 Redis 写命令「{verb}」，仅允许只读命令。"
    allowed = REDIS_READONLY_SUBCOMMANDS.get(verb)
    if allowed:
        sub = parts[1].upper() if len(parts) > 1 else ""
        if sub not in allowed:
            options = "/".join(sorted(allowed))
            return f"安全限制：Redis {verb} 仅允许只读子命令（{options}）"
    return None


def kafka_command_denied(command: str) -> str | None:
    """Kafka 命令是否被拒绝：返回拒绝原因，None 表示放行。"""
    try:
        parts = shlex.split(command or "")
    except ValueError as exc:
        return f"安全限制：Kafka 命令解析失败（{exc}）"
    if not parts:
        return "安全限制：Kafka 命令为空"
    group = parts[0]
    allowed = KAFKA_READONLY_SUBCOMMANDS.get(group)
    if allowed is None:
        return "安全限制：只允许 topics 或 consumer-groups 子命令"
    flags = [p for p in parts[1:] if p.startswith("--")]
    if not any(flag in allowed for flag in flags):
        options = "/".join(sorted(allowed))
        return f"安全限制：{group} 仅允许只读子命令（{options}）"
    dangerous = [flag for flag in flags if flag in KAFKA_DANGEROUS_FLAGS]
    if dangerous:
        return f"安全限制：拒绝 Kafka 写操作 {dangerous}"
    return None


__all__ = [
    "REDIS_READONLY_VERBS",
    "REDIS_READONLY_SUBCOMMANDS",
    "KAFKA_READONLY_SUBCOMMANDS",
    "KAFKA_DANGEROUS_FLAGS",
    "redis_command_denied",
    "kafka_command_denied",
]

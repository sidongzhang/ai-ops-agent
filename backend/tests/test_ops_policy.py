"""只读操作策略回归测试。

背景：Redis/Kafka 白名单此前在三处各维护一份且语义不一致——诊断 Agent 工具版
只校验动词，`CONFIG SET maxmemory 1mb` 这类写操作可以绕过；重构后统一到
`shared/ops_policy.py`，本测试把「写操作必须被拒」钉死，防止再次分叉。
"""

from app.services.descriptors.ops_policy import (
    kafka_command_denied,
    redis_command_denied,
)


def test_redis_readonly_commands_are_allowed():
    allowed = [
        "INFO memory",
        "DBSIZE",
        "CONFIG GET maxmemory",
        "SLOWLOG GET 10",
        "CLIENT LIST",
        "MEMORY USAGE key",
        "SCAN 0 COUNT 10",
        "GET some-key",
    ]
    for command in allowed:
        assert redis_command_denied(command) is None, command


def test_redis_write_commands_are_denied():
    denied = [
        "FLUSHALL",
        "FLUSHDB",
        "DEL some-key",
        "SET some-key value",
        "EXPIRE some-key 10",
        "CONFIG SET maxmemory 1mb",     # 重构前可绕过白名单
        "CONFIG REWRITE",               # 重构前可绕过白名单
        "CLIENT KILL ID 3",
        "SLOWLOG RESET",
        "MEMORY PURGE",
        "",
    ]
    for command in denied:
        reason = redis_command_denied(command)
        assert reason and "安全限制" in reason, command


def test_kafka_readonly_commands_are_allowed():
    allowed = [
        "consumer-groups --describe --all-groups",
        "topics --list",
        "topics --describe",
    ]
    for command in allowed:
        assert kafka_command_denied(command) is None, command


def test_kafka_write_commands_are_denied():
    denied = [
        "topics --delete --topic demo",
        "topics --create --topic demo",
        "topics --alter --topic demo",
        "consumer-groups --reset-offsets --execute --all-groups",
        "consumer-groups",
        "broker-config --list",
        "",
    ]
    for command in denied:
        reason = kafka_command_denied(command)
        assert reason and "安全限制" in reason, command

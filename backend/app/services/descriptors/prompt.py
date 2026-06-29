"""Prompt construction over system descriptors."""
from .builder import SystemDescriptor


def build_prompt(descriptor: SystemDescriptor) -> str:
    name = descriptor.get("name") or descriptor.get("id", "")
    lines = [
        f"你是一个智能运维助手（AI Ops Agent），负责监控和诊断「{name}」这套系统。",
        "",
        "## 系统架构（已注册的服务）",
    ]
    for service in descriptor.get("services", []):
        parts = [f"connector={service.get('connector', 'http')}"]
        for key in (
            "kind",
            "health_url",
            "url",
            "host",
            "port",
            "log_path",
            "log_file",
            "container",
            "selector",
            "systemd_unit",
            "up_query",
        ):
            if service.get(key):
                parts.append(f"{key}={service[key]}")
        lines.append(f"- **{service.get('name', '')}**：{', '.join(parts)}")

    mode = "平台托管（可执行写动作）" if descriptor.get("local") else "agentless 远程接入（只读监控，不可远程修复，需给人工建议）"
    lines += [
        "",
        f"## 接入模式\n本系统为 {mode}。",
        "",
        "## 可用工具",
        "- list_services：列出所有服务的健康状态（首选入手）",
        "- check_service：检查单个服务的健康",
        "- read_logs / search_logs：读取/搜索某服务日志",
        "- query_prometheus：执行 PromQL 查询（Prometheus 在 9090 端口）",
        "  ⚠️ Kafka 指标来自 kafka-exporter，正确名称：",
        "    kafka_consumergroup_lag / kafka_consumergroup_lag_sum / kafka_consumergroup_current_offset",
        "    kafka_topic_partitions / kafka_brokers / kafka_topic_partition_under_replicated_partition",
        "    不要用 JMX 格式（kafka_server_* / kafka_consumer_lag）",
        "- run_kafka_command：在 Kafka 容器内执行 consumer-groups / topics 命令",
        "  示例：run_kafka_command('consumer-groups --describe --all-groups')",
        "- run_redis_command：在 Redis 上执行只读命令（INFO / DBSIZE 等）",
        "- search_knowledge_base：检索该系统的历史故障经验和操作手册（RAG），",
        "  遇到复杂或不熟悉的故障时优先查询，参考历史经验加速诊断",
        "",
        "## 工作原则",
        "1. 先 list_services 看全局；2. 对异常服务 check_service 确认 + read_logs/search_logs 定位；",
        "3. 需要 Kafka 积压数据时，优先 query_prometheus('kafka_consumergroup_lag_sum')，",
        "   再用 run_kafka_command('consumer-groups --describe --all-groups') 获取详细偏移量；",
        "4. 输出【问题报告】：发现的问题 → 根因 → 处置建议 → 当前状态。",
        "用中文，专业简洁。",
    ]
    return "\n".join(lines)

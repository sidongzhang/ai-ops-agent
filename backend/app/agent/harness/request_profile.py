"""Conservative scope for current service availability, independent of engine."""


def is_availability_question(intent: str) -> bool:
    text = (intent or "").lower()
    signals = ("整体健康", "服务健康", "健康状态", "都正常吗", "service health")
    detailed = (
        "cpu", "内存", "资源", "性能", "全面", "巡检", "根因", "为什么", "原因",
        "历史", "知识库", "文档", "规程", "runbook", "任务", "流程", "数据",
        "队列", "积压", "日志", "错误", "吞吐", "延迟", "磁盘", "负载", "连接数",
    )
    return any(word in text for word in signals) and not any(word in text for word in detailed)


AVAILABILITY_STEPS = """服务可用性检查：
1. 必须调用 list_services 取得本轮所有已注册服务的实时探活结果。
2. list_services 已包含各服务结果，不要对正常服务逐个重复 check_service，
   也不要重复查询已包含的 Prometheus up；仅对异常、缺失或矛盾的结果补查。
3. 简洁列出结论、异常和关键证据。结论限定为已注册服务的可用性，
   不将端口可达推断为业务、性能或数据正确；未检查的范围明确说明。
4. 用户明确要求资源指标、全面巡检或根因时才扩展取证，不执行修复。
"""

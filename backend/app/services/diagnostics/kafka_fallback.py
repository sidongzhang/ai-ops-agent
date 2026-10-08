"""Kafka 积压问题的确定性兜底诊断。

从 diagnostics/service.py 拆出：当模型工具链路不可用、或识别到典型 Kafka 积压问题时，
直接用平台已采集的 Kafka 指标给出结论（不依赖 LLM），保证这类高频问题始终有答案。
"""
import time

from sqlmodel import Session

from app.models.diagnostics import DiagnosisReport
from app.schemas import DiagnoseResponse
from app.services.diagnostics.evidence import (
    build_evidence_from_tool_calls,
    build_evidence_steps,
)
from app.services.diagnostics.reports import save_diagnosis_report
from app.services.diagnostics.responses import build_diagnose_response

FALLBACK_TEMPLATE_NAME = "Kafka 积压兜底诊断"
FALLBACK_TEMPLATE_DESC = "模型工具调用失败时，使用平台已采集 Kafka 指标直接分析积压状态"
FALLBACK_MODEL = "deterministic-kafka-metrics"


def is_kafka_lag_question(question: str) -> bool:
    lowered = question.lower()
    return "kafka" in lowered and any(
        marker in lowered
        for marker in ("积压", "lag", "堆积", "消息", "consumer", "消费")
    )


def kafka_lag_fallback(
    session: Session,
    system,
    org_id: int,
    question: str,
    *,
    started_at: float,
    user_id: int | None,
    existing_report_id: int | None,
) -> DiagnoseResponse:
    from app.services.monitoring.metrics import get_metrics

    metrics = get_metrics(session, system.id, org_id)
    kafka = metrics.kafka
    if not kafka.available:
        answer = (
            "我没有拿到 Kafka 的可用指标，暂时无法判断是否存在消息积压。\n\n"
            "建议先确认 Kafka 服务已启用监控，并且 Prometheus/kafka-exporter 或采集器已经接入。"
        )
    elif kafka.consumer_groups:
        top_groups = sorted(kafka.consumer_groups, key=lambda item: item.lag, reverse=True)[:10]
        rows = "\n".join(
            f"- 消费组 `{item.group}` / Topic `{item.topic}`：Lag `{item.lag}`"
            for item in top_groups
        )
        if kafka.total_lag > 0:
            answer = (
                f"有消息积压。当前 Kafka 总 Lag 为 `{kafka.total_lag}`。\n\n"
                f"积压明细：\n{rows}\n\n"
                "建议优先检查 Lag 最高的消费组：确认消费者实例是否在线、消费线程是否阻塞、"
                "下游数据库/接口是否变慢，以及是否需要临时扩容消费者。"
            )
        else:
            answer = (
                "当前没有发现 Kafka 消息积压。已查询到消费者组指标，但总 Lag 为 `0`。\n\n"
                f"消费者组明细：\n{rows}"
            )
    else:
        answer = (
            "Kafka 当前可达，但没有查询到消费者组 Lag 数据。\n\n"
            "这通常表示暂无活跃消费者组、kafka-exporter 未采集 consumer group 指标，"
            "或当前系统没有注册对应的 Prometheus 指标。"
        )

    duration_ms = round((time.monotonic() - started_at) * 1000)
    tool_calls = [
        {
            "tool": "kafka_metrics_fallback",
            "input": {"question": question},
            "status": "success",
            "duration_ms": duration_ms,
            "output": {
                "available": kafka.available,
                "brokers": kafka.brokers,
                "topics": kafka.topics,
                "total_lag": kafka.total_lag,
                "consumer_groups": [item.model_dump() for item in kafka.consumer_groups[:20]],
            },
        }
    ]
    evidence_sources = ["Kafka 运行数据"]
    evidence_items = build_evidence_from_tool_calls(tool_calls)
    evidence_steps = build_evidence_steps(tool_calls)

    if existing_report_id:
        report = session.get(DiagnosisReport, existing_report_id)
        if not report:
            raise LookupError("诊断报告不存在")
    else:
        report = save_diagnosis_report(
            session,
            org_id=org_id,
            system_id=system.id,
            user_id=user_id,
            report_type="diagnose",
            question=question,
            answer=answer,
            template_name=FALLBACK_TEMPLATE_NAME,
            template_description=FALLBACK_TEMPLATE_DESC,
            model=FALLBACK_MODEL,
            duration_ms=duration_ms,
            evidence_sources=evidence_sources,
            evidence_steps=evidence_steps,
            tool_calls=tool_calls,
            evidence=evidence_items,
            commit=True,
        )

    return build_diagnose_response(
        report_id=report.id,
        system_id=system.id,
        answer=answer,
        template_name=FALLBACK_TEMPLATE_NAME,
        template_description=FALLBACK_TEMPLATE_DESC,
        model=FALLBACK_MODEL,
        duration_ms=duration_ms,
        evidence_sources=evidence_sources,
        tool_calls=tool_calls,
        evidence=evidence_items,
        evidence_steps=evidence_steps,
    )


__all__ = ["is_kafka_lag_question", "kafka_lag_fallback"]

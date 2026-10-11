"""趋势巡检（早期预警）：采样指标 → 线性外推 → **未到阈值**就提前提醒。

与阈值告警的分工：
  * 阈值告警回答"**已经坏了**"（服务探活失败、容器 OOM…）。
  * 趋势预警回答"**快要坏了**"：例如「MySQL 连接数 3 天涨了 60%，
    按此趋势约 2 天后打满」——在故障发生前给出提前量。

做法：定时把关键指标落成样本（metric_samples），对最近窗口做最小二乘拟合，
若增速会在 `trend_horizon_hours` 内触顶、或当前值已达阈值 `trend_warn_ratio`，
就生成一条 message_type="warning" 的消息并按系统通知配置外发。

去重：同一系统同一指标只保留**一条未解决**的预警消息，后续轮次只刷新内容（不重复打扰）。
恢复：指标不再预警时自动把该预警改为已解决。
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.core.config import settings
from app.models.messages import SystemMessage
from app.models.monitoring import MetricSample
from app.models.systems import MonitoredSystem

log = logging.getLogger(__name__)

SERIES_LIMIT = 240          # 拟合窗口：最多取最近多少条样本
WARNING_TYPE = "warning"    # message_type（与"已经坏了"的 alert 区分）


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    unit: str
    threshold: float
    min_samples: int = 4


def metric_specs() -> dict[str, MetricSpec]:
    return {
        "mysql_connections": MetricSpec(
            "mysql_connections", "MySQL 连接数", "个", float(settings.mysql_max_connections)
        ),
        "prom_mem_used_pct": MetricSpec("prom_mem_used_pct", "内存使用率", "%", 95.0),
        "prom_cpu_pct": MetricSpec("prom_cpu_pct", "CPU 使用率", "%", 95.0),
        "redis_connected_clients": MetricSpec(
            "redis_connected_clients", "Redis 连接数", "个", 800.0
        ),
        "kafka_total_lag": MetricSpec(
            "kafka_total_lag", "Kafka 积压", "条", float(settings.kafka_lag_warn)
        ),
    }


def extract_metrics(metrics) -> dict[str, float]:
    """从 SystemMetrics 抽取被跟踪指标（不可用的直接跳过）。"""
    out: dict[str, float] = {}
    prom = getattr(metrics, "prometheus", None)
    if prom is not None and getattr(prom, "available", False):
        out["prom_mem_used_pct"] = float(prom.mem_used_pct)
        out["prom_cpu_pct"] = float(prom.cpu_usage_pct)
    redis = getattr(metrics, "redis", None)
    if redis is not None and getattr(redis, "available", False):
        out["redis_connected_clients"] = float(redis.connected_clients)
    mysql = getattr(metrics, "mysql", None)
    if mysql is not None and getattr(mysql, "available", False):
        out["mysql_connections"] = float(mysql.connections)
    kafka = getattr(metrics, "kafka", None)
    if kafka is not None and getattr(kafka, "available", False):
        out["kafka_total_lag"] = float(kafka.total_lag)
    return out


def _slope_per_hour(points: list[tuple[datetime, float]]) -> float:
    """最小二乘斜率（值/小时）。"""
    n = len(points)
    if n < 2:
        return 0.0
    base = points[0][0]
    xs = [(t - base).total_seconds() / 3600.0 for t, _ in points]
    ys = [v for _, v in points]
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom <= 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom


def analyze_series(
    points: list[tuple[datetime, float]],
    spec: MetricSpec,
    *,
    horizon_hours: float | None = None,
    warn_ratio: float | None = None,
) -> dict | None:
    """对一条时间序列判断是否需要早期预警；不需要则返回 None。"""
    if not points:
        return None
    horizon = settings.trend_horizon_hours if horizon_hours is None else horizon_hours
    ratio = settings.trend_warn_ratio if warn_ratio is None else warn_ratio
    current = points[-1][1]
    base = {"current": current, "threshold": spec.threshold, "slope_per_hour": 0.0, "eta_hours": None}

    if current >= spec.threshold:
        return {
            **base,
            "level": "critical",
            "reason": f"已达上限 {spec.threshold:g}{spec.unit}（当前 {current:.0f}{spec.unit}）",
        }

    if len(points) >= spec.min_samples:
        slope = _slope_per_hour(points)
        eta = (spec.threshold - current) / slope if slope > 0 else None
        if current >= ratio * spec.threshold:
            reason = f"已达上限的 {ratio:.0%}（{current:.0f}/{spec.threshold:.0f}{spec.unit}）"
            if eta is not None and eta <= horizon:
                reason += f"，按当前增速约 {eta:.0f} 小时后触顶"
            return {**base, "level": "warning", "reason": reason, "slope_per_hour": slope, "eta_hours": eta}
        if eta is not None and eta <= horizon:
            rise = current - points[0][1]
            return {
                **base,
                "level": "warning",
                "reason": f"持续上升（近 {len(points)} 次 +{rise:.0f}{spec.unit}），按当前增速约 {eta:.0f} 小时后触顶",
                "slope_per_hour": slope,
                "eta_hours": eta,
            }
        return None

    if current >= ratio * spec.threshold:
        return {
            **base,
            "level": "warning",
            "reason": f"已达上限的 {ratio:.0%}（{current:.0f}/{spec.threshold:.0f}{spec.unit}）",
        }
    return None


# ── 采样 ────────────────────────────────────────────────────────────────
def record_samples(
    session: Session,
    system: MonitoredSystem,
    values: dict[str, float],
    *,
    observed_at: datetime | None = None,
) -> int:
    at = observed_at or utcnow()
    count = 0
    for metric, value in values.items():
        session.add(
            MetricSample(
                org_id=system.org_id, system_id=system.id, metric=metric, value=float(value), observed_at=at
            )
        )
        count += 1
    return count


def load_series(session: Session, system_id: int, metric: str, *, limit: int = SERIES_LIMIT):
    rows = session.exec(
        select(MetricSample)
        .where(MetricSample.system_id == system_id, MetricSample.metric == metric)
        .order_by(MetricSample.observed_at.desc())
        .limit(limit)
    ).all()
    return [(row.observed_at, float(row.value)) for row in reversed(rows)]


def _last_sample_at(session: Session, system_id: int) -> datetime | None:
    row = session.exec(
        select(MetricSample)
        .where(MetricSample.system_id == system_id)
        .order_by(MetricSample.observed_at.desc())
        .limit(1)
    ).first()
    if not row:
        return None
    return row.observed_at


def _is_due(session: Session, system_id: int) -> bool:
    last = _last_sample_at(session, system_id)
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (utcnow() - last).total_seconds() >= settings.trend_patrol_interval


def prune_samples(session: Session, system_id: int, *, days: int | None = None) -> None:
    days = settings.trend_retention_days if days is None else days
    cutoff = utcnow() - timedelta(days=max(1, days))
    for row in session.exec(
        select(MetricSample).where(
            MetricSample.system_id == system_id, MetricSample.observed_at < cutoff
        )
    ).all():
        session.delete(row)


# ── 预警消息 ────────────────────────────────────────────────────────────
def _open_warnings(session: Session, system: MonitoredSystem) -> list[SystemMessage]:
    return list(
        session.exec(
            select(SystemMessage).where(
                SystemMessage.system_id == system.id,
                SystemMessage.org_id == system.org_id,
                SystemMessage.message_type == WARNING_TYPE,
                SystemMessage.status != "resolved",
            )
        ).all()
    )


def _create_warning(
    session: Session, system: MonitoredSystem, metric: str, warn: dict, spec: MetricSpec
) -> SystemMessage:
    message = SystemMessage(
        org_id=system.org_id,
        system_id=system.id,
        message_type=WARNING_TYPE,
        severity="warning" if warn["level"] == "warning" else "critical",
        title=f"系统「{system.name}」指标趋势预警：{spec.label}",
        summary=f"{spec.label} {warn['current']:.0f}{spec.unit}（阈值 {spec.threshold:g}{spec.unit}）",
        content=f"平台趋势巡检发现：{spec.label} {warn['reason']}。",
        diagnosis="当前指标尚未触发阈值告警，但趋势显示将在预测窗口内触及上限，建议提前处理。",
        suggestion=[
            f"关注 {spec.label} 的变化来源，确认是业务增长还是异常泄漏/积压。",
            "结合近期变更记录判断是否与新版本/配置调整相关。",
            "进入 AI 诊断追问该指标的成因与扩容/清理步骤。",
        ],
        source="trend_patrol",
        related={
            "kind": "early_warning",
            "metric": metric,
            "label": spec.label,
            "current": warn["current"],
            "threshold": spec.threshold,
            "unit": spec.unit,
            "eta_hours": warn["eta_hours"],
            "slope_per_hour": warn["slope_per_hour"],
            "reason": warn["reason"],
        },
    )
    session.add(message)
    session.flush()
    return message


def _refresh_warning(
    session: Session, system: MonitoredSystem, message: SystemMessage, warn: dict, spec: MetricSpec
) -> None:
    related = dict(message.related or {})
    related.update(
        {
            "current": warn["current"],
            "eta_hours": warn["eta_hours"],
            "slope_per_hour": warn["slope_per_hour"],
            "reason": warn["reason"],
        }
    )
    message.related = related
    message.summary = f"{spec.label} {warn['current']:.0f}{spec.unit}（阈值 {spec.threshold:g}{spec.unit}）"
    message.content = f"平台趋势巡检发现：{spec.label} {warn['reason']}。"
    message.severity = "warning" if warn["level"] == "warning" else "critical"
    session.add(message)


def _resolve_warning(session: Session, message: SystemMessage) -> None:
    now = utcnow()
    message.status = "resolved"
    message.read_at = message.read_at or now
    message.ack_at = message.ack_at or now
    message.resolved_at = now
    label = (message.related or {}).get("label", "指标")
    message.summary = f"已恢复：{label} 趋势回落"
    message.content = f"{label} 已回落到预警线以下，趋势预警自动解除。"
    session.add(message)


def run_trend_patrol(
    session: Session,
    system: MonitoredSystem,
    *,
    metrics=None,
    force: bool = False,
) -> dict:
    """对单个系统执行一轮趋势巡检。失败不抛出（巡检不能互相拖垮）。"""
    if not settings.trend_patrol_enabled:
        return {"skipped": "disabled"}
    if not force and not _is_due(session, system.id):
        return {"skipped": "not_due"}

    if metrics is None:
        from app.services.monitoring.metrics import get_metrics

        try:
            metrics = get_metrics(session, system.id, system.org_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("[trend] 系统「%s」指标采集失败：%s", system.name, exc)
            return {"skipped": "collect_failed"}

    values = extract_metrics(metrics)
    if not values:
        prune_samples(session, system.id)
        session.commit()
        return {"sampled": 0, "warnings": 0}

    record_samples(session, system, values, observed_at=utcnow())
    specs = metric_specs()
    warnings: dict[str, dict] = {}
    for metric in values:
        spec = specs.get(metric)
        if not spec:
            continue
        result = analyze_series(load_series(session, system.id, metric), spec)
        if result:
            warnings[metric] = result

    existing = {str((m.related or {}).get("metric")): m for m in _open_warnings(session, system)}
    created = updated = 0
    new_messages: list[SystemMessage] = []
    for metric, warn in warnings.items():
        spec = specs[metric]
        message = existing.get(metric)
        if message:
            _refresh_warning(session, system, message, warn, spec)
            updated += 1
        else:
            new_messages.append(_create_warning(session, system, metric, warn, spec))
            created += 1

    resolved = 0
    for metric, message in existing.items():
        if metric not in warnings and metric != "None":
            _resolve_warning(session, message)
            resolved += 1

    prune_samples(session, system.id)
    session.commit()

    # 只对**新**预警外发通知（已有预警只刷新，避免重复打扰）
    for message in new_messages:
        try:
            from app.services.notifications.alerts import deliver_message_notifications

            deliver_message_notifications(session, system, message, actor_id="trend-patrol")
        except Exception as exc:  # noqa: BLE001
            log.warning("[trend] 预警通知发送失败 system=%s: %s", system.name, exc)

    if warnings:
        log.info(
            "[trend] 系统「%s」预警 %d 项（新建 %d / 刷新 %d / 解除 %d）",
            system.name, len(warnings), created, updated, resolved,
        )
    return {
        "sampled": len(values),
        "warnings": len(warnings),
        "created": created,
        "updated": updated,
        "resolved": resolved,
    }

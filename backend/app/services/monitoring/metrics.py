"""Metrics collection for monitored systems."""
import logging
import asyncio
import socket
import time
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel
from sqlmodel import Session

from app.repositories.systems import list_enabled_services_for_system
from app.services.descriptors.builder import system_to_descriptor
from app.services.systems.service import require_system
from app.services.collectors.exec import select_online_collector
from app.services.realtime.websocket import manager

log = logging.getLogger(__name__)


class PrometheusMetrics(BaseModel):
    available: bool = False
    mem_total_mb: float = 0
    mem_available_mb: float = 0
    mem_used_pct: float = 0
    cpu_usage_pct: float = 0
    targets_up: int = 0
    targets_total: int = 0


class RedisMetrics(BaseModel):
    available: bool = False
    used_memory_human: str = "-"
    used_memory_rss_human: str = "-"
    used_memory_peak_human: str = "-"
    connected_clients: int = 0
    total_keys: int = 0
    uptime_days: int = 0
    ops_per_sec: int = 0
    hit_rate_pct: float = 0


class KafkaConsumerGroup(BaseModel):
    group: str
    topic: str
    lag: int


class KafkaMetrics(BaseModel):
    available: bool = False
    brokers: int = 0
    topics: int = 0
    total_lag: int = 0
    consumer_groups: list[KafkaConsumerGroup] = []


class MysqlMetrics(BaseModel):
    available: bool = False
    reachable: bool = False
    connections: int = 0
    qps: float = 0
    uptime_hours: int = 0
    threads_running: int = 0


class HttpServiceStatus(BaseModel):
    name: str
    ok: bool = False
    status_code: int = 0
    latency_ms: int = 0


class SystemMetrics(BaseModel):
    prometheus: PrometheusMetrics = PrometheusMetrics()
    redis: RedisMetrics = RedisMetrics()
    kafka: KafkaMetrics = KafkaMetrics()
    mysql: MysqlMetrics = MysqlMetrics()
    http_services: list[HttpServiceStatus] = []


def get_metrics(session: Session, system_id: int, org_id: int) -> SystemMetrics:
    system = require_system(session, system_id, org_id)
    services = list_enabled_services_for_system(session, system_id)
    descriptor = system_to_descriptor(system, services)
    prom_base = find_prometheus_url(descriptor)
    remote_query, remote_range_query, remote_redis, remote_health = _remote_metric_readers(session, system, descriptor)
    return SystemMetrics(
        prometheus=collect_prometheus_metrics(prom_base, remote_query),
        redis=collect_redis_metrics(descriptor, remote_redis),
        kafka=collect_kafka_metrics(descriptor, prom_base, remote_query),
        mysql=collect_mysql_metrics(descriptor, prom_base, remote_query),
        http_services=collect_http_services(descriptor, remote_health),
    )


def _remote_metric_readers(session: Session, system, descriptor: dict):
    if system.local:
        return None, None, None, None
    collector = select_online_collector(session, system.id)
    if not collector or not manager.is_connected(collector.id):
        return None, None, None, None
    prom_service = next(
        (svc for svc in descriptor.get("services", []) if svc.get("connector") == "prometheus"),
        None,
    )

    def send(command: str, args: dict):
        try:
            result = asyncio.run(manager.send_command(collector.id, command, args))
        except Exception as exc:  # noqa: BLE001
            log.debug("[metrics] remote collector command failed: %s", exc)
            return None
        return result.get("result") if result.get("ok") else None

    def query(promql: str):
        if not prom_service:
            return []
        result = send("query_prometheus", {"service": prom_service.get("name", ""), "query": promql})
        return result if isinstance(result, list) else []

    def query_range(promql: str, start: float, end: float, step: str = "5m"):
        if not prom_service:
            return []
        result = send(
            "query_prometheus_range",
            {
                "service": prom_service.get("name", ""),
                "query": promql,
                "start": start,
                "end": end,
                "step": step,
            },
        )
        return result if isinstance(result, list) else []

    def redis_reader(command: str):
        result = send("run_redis_command", {"command": command})
        return result if isinstance(result, str) else ""

    def health_reader(service_name: str):
        result = send("health_check", {"service": service_name})
        return result[0] if isinstance(result, list) and result else None

    prom_query_fn = query if prom_service else None
    prom_range_fn = query_range if prom_service else None
    return prom_query_fn, prom_range_fn, redis_reader, health_reader


def find_prometheus_url(descriptor: dict) -> str:
    for svc in descriptor.get("services", []):
        config = svc.get("config", {})
        url = svc.get("url") or config.get("url") or svc.get("health_url") or config.get("health_url", "")
        if svc.get("connector") == "prometheus" and url:
            return url.rstrip("/")
        if url and ("9090" in url or "prometheus" in url.lower()):
            parsed = urlparse(url)
            return f"{parsed.scheme}://{parsed.netloc}"
    return descriptor.get("infra", {}).get("prometheus_url", "").rstrip("/")


def prom_query(base_url: str, promql: str, query_fn=None) -> list[dict]:
    if query_fn:
        return query_fn(promql)
    if not base_url:
        return []
    try:
        response = httpx.get(f"{base_url}/api/v1/query", params={"query": promql}, timeout=6)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") == "success":
            return payload["data"]["result"]
    except Exception as exc:
        log.debug(f"[metrics] PromQL failed ({promql}): {exc}")
    return []


def prom_query_range(
    base_url: str,
    promql: str,
    start: float,
    end: float,
    step: str = "5m",
    query_fn=None,
    range_query_fn=None,
) -> list[dict]:
    if range_query_fn:
        return range_query_fn(promql, start, end, step)
    if query_fn:
        return []
    if not base_url:
        return []
    try:
        response = httpx.get(
            f"{base_url}/api/v1/query_range",
            params={"query": promql, "start": start, "end": end, "step": step},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") == "success":
            return payload["data"]["result"]
    except Exception as exc:
        log.debug(f"[metrics] PromQL range failed ({promql}): {exc}")
    return []


def prom_scalar(base_url: str, promql: str, default: float = 0, query_fn=None) -> float:
    results = prom_query(base_url, promql, query_fn)
    if results:
        try:
            return float(results[0]["value"][1])
        except Exception:
            pass
    return default


def collect_prometheus_metrics(base_url: str, query_fn=None) -> PrometheusMetrics:
    if not base_url and not query_fn:
        return PrometheusMetrics()
    try:
        metrics = PrometheusMetrics(available=True)
        total = prom_scalar(base_url, "node_memory_MemTotal_bytes", query_fn=query_fn)
        available = prom_scalar(base_url, "node_memory_MemAvailable_bytes", query_fn=query_fn)
        metrics.mem_total_mb = round(total / 1024 / 1024, 1)
        metrics.mem_available_mb = round(available / 1024 / 1024, 1)
        if metrics.mem_total_mb > 0:
            metrics.mem_used_pct = round((1 - metrics.mem_available_mb / metrics.mem_total_mb) * 100, 1)
        metrics.cpu_usage_pct = round(
            prom_scalar(base_url, 'avg(rate(node_cpu_seconds_total{mode!="idle",mode!="iowait"}[2m])) * 100', query_fn=query_fn),
            1,
        )
        up_results = prom_query(base_url, "up", query_fn)
        metrics.targets_total = len(up_results)
        metrics.targets_up = sum(1 for result in up_results if result["value"][1] == "1")
        return metrics
    except Exception as exc:
        log.warning(f"[metrics] Prometheus 采集失败: {exc}")
        return PrometheusMetrics()


def redis_info(host: str, port: int, section: str = "", reader=None) -> dict[str, str]:
    if reader:
        raw = reader("INFO" + (f" {section}" if section else ""))
        return _parse_redis_info(raw)
    cmd_parts = ["INFO"] + ([section] if section else [])
    resp = f"*{len(cmd_parts)}\r\n" + "".join(f"${len(p)}\r\n{p}\r\n" for p in cmd_parts)
    with socket.create_connection((host, port), timeout=5) as conn:
        conn.sendall(resp.encode())
        chunks = []
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            if len(chunk) < 65536:
                break
    raw = b"".join(chunks).decode("utf-8", errors="replace")
    return _parse_redis_info(raw)


def _parse_redis_info(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" in line and not line.startswith(("#", "$", "*", "+")):
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def collect_redis_metrics(descriptor: dict, reader=None) -> RedisMetrics:
    host, port = "", 6379
    for svc in descriptor.get("services", []):
        cfg = svc.get("config", {})
        if int(cfg.get("port", 0)) == 6379 or "redis" in svc.get("name", "").lower():
            host = cfg.get("host", "127.0.0.1")
            port = int(cfg.get("port", 6379))
            break
    if not host:
        return RedisMetrics()
    try:
        info = redis_info(host, port, reader=reader)
        metrics = RedisMetrics(available=True)
        metrics.used_memory_human = info.get("used_memory_human", "-")
        metrics.used_memory_rss_human = info.get("used_memory_rss_human", "-")
        metrics.used_memory_peak_human = info.get("used_memory_peak_human", "-")
        metrics.connected_clients = int(info.get("connected_clients", 0))
        metrics.uptime_days = int(info.get("uptime_in_days", 0))
        metrics.ops_per_sec = int(info.get("instantaneous_ops_per_sec", 0))
        metrics.total_keys = sum(
            int(value.split("keys=")[1].split(",")[0])
            for key, value in info.items()
            if key.startswith("db") and "keys=" in value
        )
        hits = int(info.get("keyspace_hits", 0))
        misses = int(info.get("keyspace_misses", 0))
        if hits + misses > 0:
            metrics.hit_rate_pct = round(hits / (hits + misses) * 100, 1)
        return metrics
    except Exception as exc:
        log.warning(f"[metrics] Redis 采集失败: {exc}")
        return RedisMetrics()


def collect_kafka_metrics(descriptor: dict, prom_base: str, query_fn=None) -> KafkaMetrics:
    has_kafka = any(
        "kafka" in svc.get("name", "").lower() or int(svc.get("config", {}).get("port", 0)) == 9092
        for svc in descriptor.get("services", [])
    )
    if not has_kafka:
        return KafkaMetrics()

    kafka_host, kafka_port = "127.0.0.1", 9092
    for svc in descriptor.get("services", []):
        cfg = svc.get("config", {})
        if "kafka" in svc.get("name", "").lower() or int(cfg.get("port", 0)) == 9092:
            kafka_host = cfg.get("host", "127.0.0.1")
            kafka_port = int(cfg.get("port", 9092))
            break

    reachable = False
    try:
        with socket.create_connection((kafka_host, kafka_port), timeout=3):
            reachable = True
    except Exception:
        pass

    if not reachable and not query_fn:
        return KafkaMetrics(available=False)
    if not prom_base and not query_fn:
        return KafkaMetrics(available=reachable)

    try:
        metrics = KafkaMetrics(available=True)
        metrics.brokers = int(prom_scalar(prom_base, "kafka_brokers", query_fn=query_fn))
        metrics.topics = len(prom_query(prom_base, "count by (topic)(kafka_topic_partitions)", query_fn))
        groups: dict[tuple[str, str], int] = {}
        for result in prom_query(prom_base, "kafka_consumergroup_lag", query_fn):
            group = result["metric"].get("consumergroup", "")
            topic = result["metric"].get("topic", "")
            lag = int(float(result["value"][1]))
            groups[(group, topic)] = groups.get((group, topic), 0) + lag
        metrics.consumer_groups = [
            KafkaConsumerGroup(group=group, topic=topic, lag=lag)
            for (group, topic), lag in sorted(groups.items())
        ]
        metrics.total_lag = sum(groups.values())
        return metrics
    except Exception as exc:
        log.warning(f"[metrics] Kafka 采集失败: {exc}")
        return KafkaMetrics(available=reachable)


def collect_mysql_metrics(descriptor: dict, prom_base: str, query_fn=None) -> MysqlMetrics:
    mysql_host, mysql_port = "", 3306
    for svc in descriptor.get("services", []):
        cfg = svc.get("config", {})
        if "mysql" in svc.get("name", "").lower() or int(cfg.get("port", 0)) == 3306:
            mysql_host = cfg.get("host", "127.0.0.1")
            mysql_port = int(cfg.get("port", 3306))
            break
    if not mysql_host:
        return MysqlMetrics()

    reachable = False
    try:
        with socket.create_connection((mysql_host, mysql_port), timeout=3):
            reachable = True
    except Exception:
        pass

    metrics = MysqlMetrics(available=reachable or bool(query_fn), reachable=reachable or bool(query_fn))
    if not reachable and not query_fn:
        return metrics
    if not prom_base and not query_fn:
        return metrics

    try:
        connections = prom_scalar(prom_base, "mysql_global_status_threads_connected", query_fn=query_fn)
        qps = prom_scalar(prom_base, "rate(mysql_global_status_queries[1m])", query_fn=query_fn)
        uptime = prom_scalar(prom_base, "mysql_global_status_uptime", query_fn=query_fn)
        threads = prom_scalar(prom_base, "mysql_global_status_threads_running", query_fn=query_fn)
        if connections or qps or uptime:
            metrics.connections = int(connections)
            metrics.qps = round(qps, 1)
            metrics.uptime_hours = int(uptime // 3600)
            metrics.threads_running = int(threads)
    except Exception:
        pass
    return metrics


def collect_http_services(descriptor: dict, health_fn=None) -> list[HttpServiceStatus]:
    results: list[HttpServiceStatus] = []
    skip_ports = {9090, 9100}
    for svc in descriptor.get("services", []):
        if svc.get("connector") != "http":
            continue
        url = svc.get("health_url") or svc.get("config", {}).get("health_url", "")
        if not url or any(f":{port}" in url for port in skip_ports):
            continue
        if health_fn:
            result = health_fn(svc.get("name", "")) or {}
            results.append(HttpServiceStatus(
                name=svc.get("name", url),
                ok=bool(result.get("ok")),
                status_code=200 if result.get("ok") else 0,
            ))
            continue
        try:
            start = time.monotonic()
            response = httpx.get(url, timeout=5, follow_redirects=True)
            latency = int((time.monotonic() - start) * 1000)
            results.append(
                HttpServiceStatus(
                    name=svc.get("name", url),
                    ok=response.status_code < 400,
                    status_code=response.status_code,
                    latency_ms=latency,
                )
            )
        except Exception:
            results.append(HttpServiceStatus(name=svc.get("name", url), ok=False))
    return results


class MetricHistoryPoint(BaseModel):
    timestamp: float
    value: float


class MetricHistorySeries(BaseModel):
    key: str
    label: str
    unit: str = ""
    points: list[MetricHistoryPoint] = []


class MetricsHistoryOut(BaseModel):
    available: bool = False
    range: str = "1h"
    step: str = "5m"
    series: list[MetricHistorySeries] = []


_RANGE_SECONDS = {"1h": 3600, "6h": 21600, "24h": 86400}
_BASE_HISTORY_QUERIES = (
    ("mem_used_pct", "内存使用率", "%", "(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100"),
    ("cpu_usage_pct", "CPU 使用率", "%", 'avg(rate(node_cpu_seconds_total{mode!="idle",mode!="iowait"}[5m])) * 100'),
)
_OPTIONAL_HISTORY_QUERIES = (
    ("redis_clients", "Redis 连接数", "", "redis_connected_clients"),
    ("kafka_lag", "Kafka 总 Lag", "", "sum(kafka_consumergroup_lag)"),
)


def _history_queries_for_descriptor(descriptor: dict) -> tuple[tuple[str, str, str, str], ...]:
    queries = list(_BASE_HISTORY_QUERIES)
    services = descriptor.get("services", [])
    has_redis = any(
        "redis" in svc.get("name", "").lower() or int(svc.get("config", {}).get("port", 0) or 0) == 6379
        for svc in services
    )
    has_kafka = any("kafka" in svc.get("name", "").lower() for svc in services)
    if has_redis:
        queries.append(_OPTIONAL_HISTORY_QUERIES[0])
    if has_kafka:
        queries.append(_OPTIONAL_HISTORY_QUERIES[1])
    return tuple(queries)


def get_metrics_history(
    session: Session,
    system_id: int,
    org_id: int,
    *,
    range_key: str = "1h",
) -> MetricsHistoryOut:
    system = require_system(session, system_id, org_id)
    services = list_enabled_services_for_system(session, system_id)
    descriptor = system_to_descriptor(system, services)
    prom_base = find_prometheus_url(descriptor)
    remote_query, remote_range_query, _, _ = _remote_metric_readers(session, system, descriptor)
    if not prom_base and not remote_query and not remote_range_query:
        return MetricsHistoryOut(range=range_key)

    seconds = _RANGE_SECONDS.get(range_key, 3600)
    end = time.time()
    start = end - seconds
    step = "5m" if seconds <= 3600 else ("15m" if seconds <= 21600 else "1h")
    series: list[MetricHistorySeries] = []
    history_queries = _history_queries_for_descriptor(descriptor)

    for key, label, unit, promql in history_queries:
        results = prom_query_range(
            prom_base,
            promql,
            start,
            end,
            step,
            query_fn=remote_query,
            range_query_fn=remote_range_query,
        )
        points: list[MetricHistoryPoint] = []
        if results:
            values = results[0].get("values") or []
            for ts, val in values:
                try:
                    points.append(MetricHistoryPoint(timestamp=float(ts), value=round(float(val), 2)))
                except (TypeError, ValueError):
                    continue
        series.append(MetricHistorySeries(key=key, label=label, unit=unit, points=points))

    available = any(item.points for item in series)
    return MetricsHistoryOut(available=available, range=range_key, step=step, series=series)

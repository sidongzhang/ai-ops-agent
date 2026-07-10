"""Metrics collection for monitored systems."""
import logging
import socket
import time
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel
from sqlmodel import Session

from app.repositories.systems import list_enabled_services_for_system
from app.services.descriptors.builder import system_to_descriptor
from app.services.systems.service import require_system

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
    return SystemMetrics(
        prometheus=collect_prometheus_metrics(prom_base),
        redis=collect_redis_metrics(descriptor),
        kafka=collect_kafka_metrics(descriptor, prom_base),
        mysql=collect_mysql_metrics(descriptor, prom_base),
        http_services=collect_http_services(descriptor),
    )


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


def prom_query(base_url: str, promql: str) -> list[dict]:
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


def prom_scalar(base_url: str, promql: str, default: float = 0) -> float:
    results = prom_query(base_url, promql)
    if results:
        try:
            return float(results[0]["value"][1])
        except Exception:
            pass
    return default


def collect_prometheus_metrics(base_url: str) -> PrometheusMetrics:
    if not base_url:
        return PrometheusMetrics()
    try:
        metrics = PrometheusMetrics(available=True)
        total = prom_scalar(base_url, "node_memory_MemTotal_bytes")
        available = prom_scalar(base_url, "node_memory_MemAvailable_bytes")
        metrics.mem_total_mb = round(total / 1024 / 1024, 1)
        metrics.mem_available_mb = round(available / 1024 / 1024, 1)
        if metrics.mem_total_mb > 0:
            metrics.mem_used_pct = round((1 - metrics.mem_available_mb / metrics.mem_total_mb) * 100, 1)
        metrics.cpu_usage_pct = round(
            prom_scalar(base_url, 'avg(rate(node_cpu_seconds_total{mode!="idle",mode!="iowait"}[2m])) * 100'),
            1,
        )
        up_results = prom_query(base_url, "up")
        metrics.targets_total = len(up_results)
        metrics.targets_up = sum(1 for result in up_results if result["value"][1] == "1")
        return metrics
    except Exception as exc:
        log.warning(f"[metrics] Prometheus 采集失败: {exc}")
        return PrometheusMetrics()


def redis_info(host: str, port: int, section: str = "") -> dict[str, str]:
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
    result: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" in line and not line.startswith(("#", "$", "*", "+")):
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def collect_redis_metrics(descriptor: dict) -> RedisMetrics:
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
        info = redis_info(host, port)
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


def collect_kafka_metrics(descriptor: dict, prom_base: str) -> KafkaMetrics:
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

    if not reachable or not prom_base:
        return KafkaMetrics(available=reachable)

    try:
        metrics = KafkaMetrics(available=True)
        metrics.brokers = int(prom_scalar(prom_base, "kafka_brokers"))
        metrics.topics = len(prom_query(prom_base, "count by (topic)(kafka_topic_partitions)"))
        groups: dict[tuple[str, str], int] = {}
        for result in prom_query(prom_base, "kafka_consumergroup_lag"):
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


def collect_mysql_metrics(descriptor: dict, prom_base: str) -> MysqlMetrics:
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

    metrics = MysqlMetrics(available=reachable, reachable=reachable)
    if not reachable or not prom_base:
        return metrics

    try:
        connections = prom_scalar(prom_base, "mysql_global_status_threads_connected")
        qps = prom_scalar(prom_base, "rate(mysql_global_status_queries[1m])")
        uptime = prom_scalar(prom_base, "mysql_global_status_uptime")
        threads = prom_scalar(prom_base, "mysql_global_status_threads_running")
        if connections or qps or uptime:
            metrics.connections = int(connections)
            metrics.qps = round(qps, 1)
            metrics.uptime_hours = int(uptime // 3600)
            metrics.threads_running = int(threads)
    except Exception:
        pass
    return metrics


def collect_http_services(descriptor: dict) -> list[HttpServiceStatus]:
    results: list[HttpServiceStatus] = []
    skip_ports = {9090, 9100}
    for svc in descriptor.get("services", []):
        if svc.get("connector") != "http":
            continue
        url = svc.get("health_url") or svc.get("config", {}).get("health_url", "")
        if not url or any(f":{port}" in url for port in skip_ports):
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

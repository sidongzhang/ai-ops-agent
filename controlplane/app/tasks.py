"""
Celery 定时巡检任务。

Worker/Beat 独立进程，不能用 FastAPI 的 Depends，直接用 SQLModel Session。
每次巡检：
  1. 遍历所有 MonitoredSystem
  2. 有 Collector 且最近上报过 → 跳过（采集器自己负责，不重复探）
  3. 无 Collector（公网/直连系统）→ collect_health() 探活，写入快照
  4. 发现新增 FAIL 服务 → 冷却期外则触发告警（Webhook POST）
"""
import logging
import time
from datetime import datetime, timezone

import httpx
from sqlmodel import Session, select

from .celery_app import celery
from .config import settings
from .db import engine
from .descriptors import collect_health, system_to_descriptor
from .models import Collector, MonitoredSystem, Service

log = logging.getLogger(__name__)

_COLLECTOR_STALE_MULTIPLIER = 2   # 超过 interval × 2 秒未上报则视为失联，平台接管探活


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _services_of(session: Session, system_id: int) -> list[Service]:
    return list(session.exec(select(Service).where(Service.system_id == system_id)))


def _has_fresh_collector(system: MonitoredSystem, session: Session) -> bool:
    """该系统是否有存活的采集器（最近有上报）。"""
    collector = session.exec(
        select(Collector).where(Collector.system_id == system.id)
    ).first()
    if not collector or not system.last_report_at:
        return False
    elapsed = (_utcnow() - system.last_report_at.replace(tzinfo=timezone.utc)).total_seconds()
    # 若采集器在 2 倍巡检周期内有过上报，认为它存活
    return elapsed < settings.health_check_interval * _COLLECTOR_STALE_MULTIPLIER


def _newly_failed(old_health: dict, new_results: list[dict]) -> list[str]:
    """返回「本次新变为 FAIL」的服务名列表（边沿触发，避免重复告警）。"""
    old_map = {s["name"]: s.get("ok", True) for s in old_health.get("services", [])}
    return [
        r["name"]
        for r in new_results
        if not r["ok"] and old_map.get(r["name"], True)
    ]


def _send_webhook(url: str, system_name: str, failed_services: list[str]) -> None:
    """向 Webhook URL 发送告警（飞书/钉钉/Slack incoming webhook 格式兼容）。"""
    lines = [f"🚨 系统「{system_name}」告警：以下服务异常"]
    lines.extend(f"  ✗ {s}" for s in failed_services)
    text = "\n".join(lines)

    # 飞书 incoming webhook 格式
    payload = {"msg_type": "text", "content": {"text": text}}
    try:
        resp = httpx.post(url, json=payload, timeout=8)
        log.info(f"[alert] webhook → {url} status={resp.status_code}")
    except Exception as e:
        log.warning(f"[alert] webhook 发送失败 {url}: {e}")


def _alert_if_needed(
    system: MonitoredSystem,
    new_results: list[dict],
    session: Session,
) -> None:
    """边沿触发 + 冷却期：仅在「新出现 FAIL」且「距上次告警超过冷却期」时发送告警。"""
    newly = _newly_failed(system.last_health or {}, new_results)
    if not newly:
        return

    now = _utcnow()
    if system.last_alert_at:
        elapsed = (now - system.last_alert_at.replace(tzinfo=timezone.utc)).total_seconds()
        if elapsed < settings.alert_cooldown_seconds:
            log.info(
                f"[alert] 系统「{system.name}」冷却期内（{elapsed:.0f}s < "
                f"{settings.alert_cooldown_seconds}s），跳过告警"
            )
            return

    log.warning(f"[alert] 系统「{system.name}」新异常服务: {newly}")

    webhook_url = (system.notify or {}).get("webhook_url")
    if webhook_url:
        _send_webhook(webhook_url, system.name, newly)
    else:
        log.info(f"[alert] 系统「{system.name}」未配置 notify.webhook_url，仅记录日志")

    system.last_alert_at = now
    session.add(system)


@celery.task(name="app.tasks.health_check_all_systems", bind=True, max_retries=2)
def health_check_all_systems(self):
    """定时巡检：遍历全部系统，直连探活并按需触发告警。"""
    start = time.monotonic()
    checked = skipped = errors = 0

    with Session(engine) as session:
        systems = session.exec(select(MonitoredSystem)).all()
        for system in systems:
            try:
                if _has_fresh_collector(system, session):
                    skipped += 1
                    log.debug(f"[check] 系统「{system.name}」由采集器负责，跳过")
                    continue

                services = _services_of(session, system.id)
                if not services:
                    skipped += 1
                    continue

                descriptor = system_to_descriptor(system, services)
                results = collect_health(descriptor)

                _alert_if_needed(system, results, session)

                system.last_health = {"services": results}
                system.last_report_at = _utcnow()
                session.add(system)
                checked += 1

            except Exception as e:
                errors += 1
                log.error(f"[check] 系统「{system.name}」巡检出错: {e}", exc_info=True)

        session.commit()

    elapsed = time.monotonic() - start
    log.info(
        f"[check] 本轮完成：checked={checked} skipped={skipped} errors={errors} "
        f"elapsed={elapsed:.2f}s"
    )
    return {"checked": checked, "skipped": skipped, "errors": errors}

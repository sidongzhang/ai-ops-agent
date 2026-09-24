"""Celery 应用单例。在 Worker/Beat 进程中 import 此模块来启动。"""
from celery import Celery
from celery.schedules import crontab

from ..core.config import settings

celery = Celery(
    "aiops",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery.conf.beat_schedule = {
    "health-check-all-systems": {
        "task": "app.tasks.health_check_all_systems",
        # Beat 只负责高频触发，具体系统是否到巡检时间由 monitoring.interval_seconds 决定。
        "schedule": 15.0,
    },
    # 每天上海时间 08:00 生成昨日健康日报（仅处理显式启用日报的系统）
    "daily-health-reports": {
        "task": "app.tasks.daily_health_reports",
        "schedule": crontab(hour=8, minute=0),
    },
}
celery.conf.timezone = "Asia/Shanghai"
celery.conf.task_serializer = "json"
celery.conf.result_serializer = "json"
celery.conf.accept_content = ["json"]

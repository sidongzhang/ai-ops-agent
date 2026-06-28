"""Celery 应用单例。在 Worker/Beat 进程中 import 此模块来启动。"""
from celery import Celery

from .config import settings

celery = Celery(
    "aiops",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks"],
)

celery.conf.beat_schedule = {
    "health-check-all-systems": {
        "task": "app.tasks.health_check_all_systems",
        "schedule": float(settings.health_check_interval),
    },
}
celery.conf.timezone = "UTC"
celery.conf.task_serializer = "json"
celery.conf.result_serializer = "json"
celery.conf.accept_content = ["json"]

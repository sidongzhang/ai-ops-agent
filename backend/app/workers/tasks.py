"""Celery scheduled health-check tasks."""

from .celery import celery
from ..services.monitoring.scheduled import run_scheduled_health_checks


@celery.task(name="app.tasks.health_check_all_systems", bind=True, max_retries=2)
def health_check_all_systems(self):
    return run_scheduled_health_checks()

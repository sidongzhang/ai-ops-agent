"""Celery scheduled health-check tasks."""

from .celery import celery
from ..services.monitoring.scheduled import run_scheduled_health_checks
from ..services.log_analysis import process_uploaded_log_analysis
from ..services.message_processing import process_message_by_id


@celery.task(name="app.tasks.health_check_all_systems", bind=True, max_retries=2)
def health_check_all_systems(self):
    return run_scheduled_health_checks()


@celery.task(name="app.tasks.process_open_message", bind=True, max_retries=2)
def process_open_message(self, message_id: int):
    result = process_message_by_id(message_id)
    if not result.get("ok") and self.request.retries < self.max_retries:
        raise self.retry(countdown=10)
    return result


@celery.task(name="app.tasks.process_open_log_analysis", bind=True, max_retries=2)
def process_open_log_analysis(
    self,
    message_id: int,
    filename: str,
    log_format: str,
    content: str,
    question: str,
    truncated: bool,
    token_id: int,
):
    result = process_uploaded_log_analysis(
        message_id,
        filename=filename,
        log_format=log_format,
        content=content,
        question=question,
        truncated=truncated,
        token_id=token_id,
    )
    if not result.get("ok") and self.request.retries < self.max_retries:
        raise self.retry(countdown=10)
    return result

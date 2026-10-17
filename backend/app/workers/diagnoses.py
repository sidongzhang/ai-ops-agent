"""Dedicated queue; no automatic retry of started diagnosis work."""
from .celery import celery


@celery.task(name="app.tasks.diagnosis", acks_late=True, reject_on_worker_lost=True,
             ignore_result=True, max_retries=0)
def diagnosis(report_id: int):
    from app.services.diagnostics.jobs import execute_job
    execute_job(report_id)


from celery.signals import worker_process_init


@worker_process_init.connect
def reset_inherited_connections(**kwargs):
    # Prefork children must not reuse the parent's database connections.
    from app.core.database import engine
    engine.dispose(close=False)

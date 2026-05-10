"""Celery application instance para ChatPro.

Autodiscover tasks desde los módulos de conectores y otros workers.
"""

from celery import Celery

from src.config import get_settings


def create_celery_app() -> Celery:
    settings = get_settings()
    app = Celery(
        "chatpro",
        broker=settings.redis_url,
        backend=settings.redis_url,
    )
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
    )
    app.autodiscover_tasks(["src.connectors.woocommerce", "src.billing"])
    return app


celery_app = create_celery_app()

"""Celery application for background SLA processing.

The SLA sweep is the one piece of the platform that must run without a citizen
or officer triggering it: deadlines pass on their own. Redis is the broker.

Run a worker:   celery -A app.worker.celery_app worker --loglevel=info
Run the beat:   celery -A app.worker.celery_app beat --loglevel=info

The same sweep is exposed as ``POST /admin/sla/sweep`` so the lifecycle can be
demonstrated without a worker process running.
"""

from __future__ import annotations

import logging

from celery import Celery
from celery.schedules import crontab

from app.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "barathseva",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,
    beat_schedule={
        "sla-sweep-every-5-minutes": {
            "task": "barathseva.sla_sweep",
            "schedule": crontab(minute="*/5"),
        },
    },
)


@celery_app.task(name="barathseva.sla_sweep")
def sla_sweep_task(limit: int = 500) -> dict:
    """Mark SLA breaches and raise escalations. Idempotent."""
    from app.agents.sla_monitor import sweep
    from app.db import SessionLocal

    with SessionLocal() as db:
        result = sweep(db, limit=limit)
    logger.info(
        "sla sweep: checked=%s breached=%s escalated=%s",
        result["checked"],
        len(result["newly_breached"]),
        len(result["newly_escalated"]),
    )
    return result


@celery_app.task(name="barathseva.run_intake")
def run_intake_task(complaint_id: int, channel: str = "web") -> dict:
    """Run the intake graph out-of-band.

    The HTTP path runs the pipeline inline so a citizen sees their ticket
    immediately. This task exists for retrying a complaint whose pipeline
    failed midway, and for any future out-of-band intake channel.
    """
    from app.workflow.graph import run_intake

    state = dict(run_intake(complaint_id, channel=channel))
    return {k: v for k, v in state.items() if isinstance(v, (str, int, float, bool, list))}

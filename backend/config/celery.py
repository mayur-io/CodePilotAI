"""Celery application for CodePilotAI.

Agent runs are long-lived (30s-3min), so they must never block an HTTP worker.
"""

from __future__ import annotations

import os

from celery import Celery, Task
from celery.signals import setup_logging

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("codepilotai")

# All Celery config lives in Django settings under the CELERY_ namespace.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Discover tasks.py modules in every installed app.
app.autodiscover_tasks()


@setup_logging.connect
def _configure_logging(**_: object) -> None:
    """Let Django own logging configuration; don't let Celery clobber it."""
    import logging.config

    from django.conf import settings

    logging.config.dictConfig(settings.LOGGING)


@app.task(bind=True, ignore_result=True)
def debug_task(self: Task) -> None:  # pragma: no cover - operational helper
    print(f"Request: {self.request!r}")  # noqa: T201

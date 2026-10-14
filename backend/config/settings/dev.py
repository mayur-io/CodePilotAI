"""Development settings: verbose, SQLite-friendly, no required external services."""

from __future__ import annotations

from config.settings.base import *
from config.settings.base import BASE_DIR, REDIS_URL, env

DEBUG = env.bool("DJANGO_DEBUG", default=True)

# Loopback + the Android emulator host alias (10.0.2.2) and LAN addresses.
ALLOWED_HOSTS = ["*"]
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://10.0.2.2:8000",
]
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# SQLite ignores connection limits; keep it explicit so prod tightening never leaks here.
DATABASES["default"]["CONN_MAX_AGE"] = 0

# Run Celery tasks inline unless a broker is actually reachable, so `manage.py`
# works out of the box on a fresh clone with no Redis installed.
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=True)

WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

__all__ = ["BASE_DIR", "REDIS_URL"]

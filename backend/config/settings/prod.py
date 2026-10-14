"""Production settings: strict security posture, Postgres + Redis required."""

from __future__ import annotations

from config.settings.base import *
from config.settings.base import env

DEBUG = False

# ── Security headers ────────────────────────────────────────────────────────
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = False  # the Android client reads the CSRF cookie during OAuth
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

DATABASES["default"]["CONN_MAX_AGE"] = 60
DATABASES["default"]["OPTIONS"] = {
    "sslmode": env("POSTGRES_SSLMODE", default="require"),
}

# Cache-backed throttling needs a shared store across workers.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

# No eager execution in production: agent runs must happen in real workers.
CELERY_TASK_ALWAYS_EAGER = False

ADMINS = [tuple(item.split(":", 1)) for item in env.list("DJANGO_ADMINS", default=[])]

# ── Deployment guards ───────────────────────────────────────────────────────
# Fail fast and loudly rather than booting a production box with dev defaults.
_INSECURE_SECRET = "insecure-dev-key-do-not-use-in-production"
if SECRET_KEY == _INSECURE_SECRET or len(SECRET_KEY) < 32:
    raise RuntimeError(
        "DJANGO_SECRET_KEY must be set to a unique value of at least 32 characters in production."
    )

if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
    raise RuntimeError("GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET must be set in production.")

if not TOKEN_ENCRYPTION_KEY:
    raise RuntimeError("TOKEN_ENCRYPTION_KEY must be set in production.")

if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY must be set in production.")

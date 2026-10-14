"""Base settings shared by every environment.

All environment-specific values come from environment variables (or a ``.env``
file at the repository root). Nothing secret is hardcoded.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import environ

# ── Paths ────────────────────────────────────────────────────────────────────
# config/settings/base.py -> config/settings -> config -> backend/
BASE_DIR = Path(__file__).resolve().parent.parent.parent
REPO_ROOT = BASE_DIR.parent

env = environ.Env()

# Real environment variables always win; the .env file is only a local convenience.
_ENV_FILE = REPO_ROOT / ".env"
if _ENV_FILE.is_file():
    env.read_env(str(_ENV_FILE))


def _csv(name: str, default: str = "") -> list[str]:
    """Parse a comma-separated environment variable into a clean list."""
    return [item.strip() for item in env.list(name, default=default) if item.strip()]


# ── Core ─────────────────────────────────────────────────────────────────────
SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key-do-not-use-in-production")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = _csv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Applies structlog to stdlib logging as soon as Django finishes loading settings.
READY_HOOK = "config.apps.ReadyHook"

INSTALLED_APPS = [
    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # Third party
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "corsheaders",
    "drf_spectacular",
    # Local
    "apps.accounts",
    "apps.repositories",
    "apps.agent_tasks",
    "apps.git",
    "apps.agent",
    "apps.notifications",
    "apps.dashboard",
]

SITE_ID = 1

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.gzip.GZipMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ── Database ─────────────────────────────────────────────────────────────────
DATABASES = {"default": env.db_url("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}

# ── Auth ─────────────────────────────────────────────────────────────────────
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Session login for the staff operations dashboard (apps.dashboard). Ordinary
# users have no password -- they are GitHub identities -- so an operator is
# provisioned out-of-band with an explicit password; see the README.
LOGIN_URL = "dashboard:login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "dashboard:home"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PAGINATION_CLASS": "config.api.pagination.DefaultPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "config.api.exceptions.copilot_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": ("config.api.throttling.ScopedBurstThrottle",),
    # A missing scope is a KeyError at request time, not a startup failure, so
    # every scope referenced by a view must be listed here.
    # `auth` is the tightest: handoff exchange is unauthenticated.
    "DEFAULT_THROTTLE_RATES": {
        "auth": "10/min",
        "github": "120/min",
        "agent_task": "60/min",
    },
    "DATETIME_FORMAT": "iso-8601",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": False,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "CodePilotAI API",
    "DESCRIPTION": "Mobile-first agentic AI coding assistant.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "SWAGGER_UI_SETTINGS": {"persistAuthorization": True},
    "ENUM_NAME_OVERRIDES": {
        "AgentTaskStatusEnum": "apps.agent_tasks.models.AgentTask.Status.choices",
        "ProposedChangeStatusEnum": "apps.agent_tasks.models.ProposedChange.Status.choices",
        "ProposedChangeTypeEnum": "apps.agent_tasks.models.ProposedChange.ChangeType.choices",
    },
}

# ── CORS ────────────────────────────────────────────────────────────────────
CORS_ALLOWED_ORIGINS = _csv("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = (
    "accept",
    "accept-language",
    "authorization",
    "content-type",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
)
CSRF_TRUSTED_ORIGINS = _csv("CORS_ALLOWED_ORIGINS")

# ── i18n / tz ───────────────────────────────────────────────────────────────
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ── Static / media ──────────────────────────────────────────────────────────
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# ── Celery ──────────────────────────────────────────────────────────────────
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 60 * 30
CELERY_TASK_SOFT_TIME_LIMIT = 60 * 25
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # long tasks: fair dispatch, no hoarding

# ── Groq / LLM ──────────────────────────────────────────────────────────────
GROQ_API_KEY = env("GROQ_API_KEY", default="")
GROQ_BASE_URL = env("GROQ_BASE_URL", default="https://api.groq.com/openai/v1")
GROQ_MODEL = env("GROQ_MODEL", default="openai/gpt-oss-120b")
# gpt-oss accepts only low | medium | high.
GROQ_REASONING_EFFORT = env("GROQ_REASONING_EFFORT", default="medium")
GROQ_TIMEOUT_SECONDS = env.int("GROQ_TIMEOUT_SECONDS", default=120)
GROQ_MAX_RETRIES = env.int("GROQ_MAX_RETRIES", default=4)
GROQ_TEMPERATURE = 0.6
GROQ_TOP_P = 0.95

# ── GitHub OAuth ────────────────────────────────────────────────────────────
GITHUB_CLIENT_ID = env("GITHUB_CLIENT_ID", default="")
GITHUB_CLIENT_SECRET = env("GITHUB_CLIENT_SECRET", default="")
GITHUB_REDIRECT_URI = env(
    "GITHUB_REDIRECT_URI",
    default="http://localhost:8000/api/v1/auth/github/callback",
)
GITHUB_OAUTH_SCOPE = env.list("GITHUB_OAUTH_SCOPE", default=["repo", "read:user", "user:email"])
GITHUB_API_BASE_URL = env("GITHUB_API_BASE_URL", default="https://api.github.com")
# Host used to build git remote URLs. Kept separate from GITHUB_API_BASE_URL so
# an Enterprise deployment can point the REST API at a different hostname.
GITHUB_HOST = env("GITHUB_HOST", default="github.com")
# Username embedded in the git remote URL. It is not a secret, and including it
# means git asks only for a *password*, so the token is supplied exactly once by
# the askpass helper instead of by anything on the command line or on disk.
GITHUB_GIT_USERNAME = env("GITHUB_GIT_USERNAME", default="x-access-token")
# The remote URL, used for the initial clone and stored as `remote.origin.url` in
# the mirror. It deliberately has no credential slot. A URL carrying a token
# would end up in `<mirror>/config`, where it outlives the process, and git
# echoes URLs verbatim inside fatal errors.
GITHUB_REMOTE_URL_TEMPLATE = env(
    "GITHUB_REMOTE_URL_TEMPLATE",
    default="https://{username}@{host}/{full_name}.git",
)
# Allow git to talk to remotes on the local filesystem or `file://`. Off in
# every real deployment: a repository is not allowed to redirect our fetch or
# push at a path on this worker. Turned on only for the test suite, which uses
# a bare repository in tmp_path as a stand-in for GitHub.
GIT_ALLOW_LOCAL_REMOTE = env.bool("GIT_ALLOW_LOCAL_REMOTE", default=False)

# Fernet key protecting GitHub access tokens at rest.
TOKEN_ENCRYPTION_KEY = env("TOKEN_ENCRYPTION_KEY", default="")

# ── Agent guardrails ────────────────────────────────────────────────────────
# Master switch for the LangGraph agent pipeline. Off until apps.agent is wired
# up, so task creation queues cleanly instead of dispatching a doomed run.
AGENT_PIPELINE_ENABLED = env.bool("AGENT_PIPELINE_ENABLED", default=True)
AGENT_MAX_TOOL_CALLS = env.int("AGENT_MAX_TOOL_CALLS", default=25)
AGENT_MAX_ITERATIONS = env.int("AGENT_MAX_ITERATIONS", default=8)
AGENT_MAX_REPAIR_ATTEMPTS = env.int("AGENT_MAX_REPAIR_ATTEMPTS", default=2)
AGENT_TOKEN_BUDGET = env.int("AGENT_TOKEN_BUDGET", default=120_000)
AGENT_WALL_CLOCK_SECONDS = env.int("AGENT_WALL_CLOCK_SECONDS", default=600)
AGENT_DAILY_TOKEN_BUDGET = env.int("AGENT_DAILY_TOKEN_BUDGET", default=2_000_000)

# ── Git workspaces ──────────────────────────────────────────────────────────
WORKSPACE_ROOT = Path(env("WORKSPACE_ROOT", default=str(BASE_DIR / "workspaces")))
GIT_AUTHOR_NAME = env("GIT_AUTHOR_NAME", default="CodePilotAI")
GIT_AUTHOR_EMAIL = env("GIT_AUTHOR_EMAIL", default="codepilotai@users.noreply.github.com")

# ── Push notifications (Firebase Cloud Messaging) ───────────────────────────
# Off until credentials exist. With no project configured the transport is a
# no-op, so task runs behave identically with or without push.
FCM_ENABLED = env.bool("FCM_ENABLED", default=True)
FCM_PROJECT_ID = env("FCM_PROJECT_ID", default="")
# Raw service-account JSON, preferred for container secrets. Falls back to
# FCM_SERVICE_ACCOUNT_FILE when empty. Blank means "not configured".
FCM_SERVICE_ACCOUNT_JSON = env("FCM_SERVICE_ACCOUNT_JSON", default="")
FCM_SERVICE_ACCOUNT_FILE = env("FCM_SERVICE_ACCOUNT_FILE", default="")
FCM_BASE_URL = env("FCM_BASE_URL", default="https://fcm.googleapis.com")
FCM_TOKEN_URI = env("FCM_TOKEN_URI", default="https://oauth2.googleapis.com/token")
FCM_TIMEOUT_SECONDS = env.int("FCM_TIMEOUT_SECONDS", default=10)

# ── Observability ───────────────────────────────────────────────────────────
LOG_LEVEL = env("LOG_LEVEL", default="INFO")
SENTRY_DSN = env("SENTRY_DSN", default="")

# ── App-specific settings (namespaced) ──────────────────────────────────────
ANDROID_DEEP_LINK_SCHEME = env("ANDROID_DEEP_LINK_SCHEME", default="codepilotai")

# ── Logging ─────────────────────────────────────────────────────────────────
LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "config.observability.JSONFormatter",
        },
        "plain": {
            "format": "%(asctime)s %(levelname)-8s %(name)s | %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "plain",
        },
        "error_console": {
            "class": "logging.StreamHandler",
            "level": "ERROR",
            "formatter": "plain",
        },
    },
    "root": {"handlers": ["console", "error_console"], "level": LOG_LEVEL},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "django.security": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "httpx": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "httpcore": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "urllib3": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "celery": {"level": "INFO", "handlers": ["console"], "propagate": False},
        "langchain": {"level": "WARNING", "handlers": ["console"], "propagate": False},
    },
}

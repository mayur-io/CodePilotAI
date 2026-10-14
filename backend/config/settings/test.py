"""Test settings: in-memory database, eager Celery, no network, fast password hashing."""

from __future__ import annotations

import tempfile
from pathlib import Path

from config.settings.base import *
from config.settings.base import LOGGING

DEBUG = False
SECRET_KEY = "test-only-secret-key-not-used-anywhere-real"

ALLOWED_HOSTS = ["*", "testserver"]

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

# Tests must never reach a real broker.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# The pipeline needs a real Groq key and a real GitHub token; both are blank in
# tests. Keep it disabled here so creation is deterministic, and enable it per
# test with the `settings` fixture when the dispatch path itself is under test.
AGENT_PIPELINE_ENABLED = False

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Ephemeral workspace tree so tests never touch the real one.
WORKSPACE_ROOT = Path(tempfile.mkdtemp(prefix="codepilotai-test-"))

# Blank credentials so a missing .env can never trigger a real network call.
GROQ_API_KEY = ""
GITHUB_CLIENT_ID = ""
GITHUB_CLIENT_SECRET = ""

# Deterministic valid Fernet key: DEBUG stays False in tests so that any
# accidental reliance on DEBUG-only behaviour fails loudly instead of passing.
TOKEN_ENCRYPTION_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

LOGGING["root"]["level"] = "CRITICAL"

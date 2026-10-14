"""Symmetric encryption for GitHub credentials at rest.

GitHub access tokens are long-lived and grant full repository access, so they
are encrypted with Fernet before touching the database. The key never leaves the
environment.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from config.observability import get_logger

logger = get_logger(__name__)

# Valid 32-byte urlsafe-base64 key, used only when DEBUG is on or under test so a
# fresh clone boots without a generated secret. Never used in production.
_INSECURE_DEV_KEY = b"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


@lru_cache(maxsize=1)
def _cipher() -> Fernet:
    """Return the process-wide Fernet, building it from settings on first use."""
    key = settings.TOKEN_ENCRYPTION_KEY

    if key:
        raw = key.encode() if isinstance(key, str) else key
    elif settings.DEBUG:
        logger.warning(
            "TOKEN_ENCRYPTION_KEY is unset; using an insecure development key. "
            "Generate a real one before deploying."
        )
        raw = _INSECURE_DEV_KEY
    else:
        raise ImproperlyConfigured("TOKEN_ENCRYPTION_KEY must be set when DJANGO_DEBUG is False.")

    try:
        return Fernet(raw)
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            "TOKEN_ENCRYPTION_KEY is not a valid Fernet key. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        ) from exc


def generate_key() -> str:
    """Return a fresh Fernet key suitable for ``TOKEN_ENCRYPTION_KEY``."""
    return Fernet.generate_key().decode()


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token for storage. Empty input stays empty."""
    if not plaintext:
        return ""
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a stored token.

    Returns an empty string when the value is missing or cannot be decrypted, so
    a rotated key degrades to "user must re-authenticate" instead of 500ing the
    request.
    """
    if not ciphertext:
        return ""
    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        logger.error("token_decryption_failed")
        return ""


def scrub_secrets(text: str, *secrets: str | None) -> str:
    """Replace any known secret in ``text`` with a redaction marker.

    Applied to every piece of external output (git stderr, HTTP bodies) before it
    reaches a log record, a traceback, or the mobile client.
    """
    cleaned = text
    for secret in secrets:
        if secret and len(secret) > 6:
            cleaned = cleaned.replace(secret, "***REDACTED***")
    return cleaned

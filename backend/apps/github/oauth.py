"""GitHub OAuth 2.0 web application flow.

CodePilotAI is a native app with no cookie jar to rely on, so the ``state``
parameter is a signed, expiring token rather than a server-side session value.
On callback the user's JWTs are handed back through a single-use exchange code
instead of being placed in the redirect URL, where they could leak via browser
history, referrer headers, or server access logs.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

import httpx
from django.conf import settings
from django.core import signing

from apps.accounts.crypto import scrub_secrets
from apps.github.exceptions import GitHubAuthError, GitHubError
from config.observability import get_logger

logger = get_logger(__name__)

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
STATE_MAX_AGE_SECONDS = 10 * 60
STATE_SALT = "codepilotai.github.oauth.state"


def generate_state() -> str:
    """Return a signed, expiring ``state`` value bound to a fresh nonce."""
    nonce = secrets.token_urlsafe(24)
    return signing.dumps({"nonce": nonce}, salt=STATE_SALT, compress=True)


def verify_state(state: str) -> bool:
    """Validate a ``state`` token's signature and age. Never raises."""
    try:
        signing.loads(state, salt=STATE_SALT, max_age=STATE_MAX_AGE_SECONDS)
    except signing.BadSignature:
        return False
    return True


def build_authorize_url(state: str) -> str:
    """Build the GitHub URL the app opens in a Custom Tab."""
    params = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "redirect_uri": settings.GITHUB_REDIRECT_URI,
        "scope": " ".join(settings.GITHUB_OAUTH_SCOPE),
        "state": state,
        "allow_signup": "true",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str) -> dict[str, str | int | None]:
    """Trade an authorization ``code`` for a GitHub access token."""
    payload = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "client_secret": settings.GITHUB_CLIENT_SECRET,
        "code": code,
        "redirect_uri": settings.GITHUB_REDIRECT_URI,
    }
    # `data=` makes httpx send application/x-www-form-urlencoded, which is what
    # GitHub expects. Do NOT force Content-Type: application/json here: GitHub
    # then tries to parse the form body as JSON and answers with a bare HTTP 400.
    headers = {"Accept": "application/json"}

    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(TOKEN_URL, data=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise GitHubError(f"Could not reach GitHub to exchange the code: {exc}") from exc

    if response.status_code != 200:
        raise GitHubError(f"GitHub token exchange failed (HTTP {response.status_code}).")

    try:
        data = response.json()
    except ValueError as exc:
        raise GitHubError("GitHub returned a non-JSON token response.") from exc

    if "error" in data:
        # The machine-readable code identifies the failure; the description is
        # only a hint and may be absent. Include both so a support log is useful.
        code = scrub_secrets(str(data["error"]))
        description = scrub_secrets(str(data.get("error_description") or ""))
        detail = f"{code}: {description}" if description else code
        raise GitHubAuthError(f"GitHub rejected the authorization code ({detail})")

    access_token = data.get("access_token")
    if not access_token:
        raise GitHubAuthError("GitHub did not return an access token.")

    return {
        "access_token": str(access_token),
        "refresh_token": str(data.get("refresh_token") or ""),
        "scope": str(data.get("scope") or ""),
        "token_type": str(data.get("token_type") or "bearer"),
        "expires_in": _optional_int(data.get("expires_in")),
    }


def _optional_int(value: object) -> int | None:
    """Coerce a GitHub ``expires_in`` value to an int, or return None."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return int(value)
    except ValueError:
        return None


__all__ = [
    "build_authorize_url",
    "exchange_code_for_token",
    "generate_state",
    "verify_state",
]

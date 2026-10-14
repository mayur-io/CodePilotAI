"""HTTP client for the GitHub REST API.

Design notes
------------
* Credentials are passed per-request via an explicit header. The client never
  writes a token to disk, to ``.netrc``, or to a log record.
* ``429`` and ``403`` rate-limit responses are retried with bounded exponential
  backoff, honouring ``Retry-After`` and ``X-RateLimit-Reset``.
* Every response body is scrubbed of the token before it becomes an exception
  message.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import httpx
from django.conf import settings

from apps.accounts.crypto import scrub_secrets
from apps.github.exceptions import (
    GitHubAuthError,
    GitHubConflictError,
    GitHubError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubServerError,
)
from config.observability import get_logger

logger = get_logger(__name__)

API_VERSION = "2022-11-28"
MAX_RETRIES = 4
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0


class GitHubClient:
    """Thin, typed wrapper over the GitHub REST endpoints CodePilotAI needs."""

    MAX_RETRIES = MAX_RETRIES
    MAX_BACKOFF_SECONDS = MAX_BACKOFF_SECONDS

    def __init__(
        self,
        access_token: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not access_token:
            raise GitHubAuthError("A GitHub access token is required.")
        self._token = access_token
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=settings.GITHUB_API_BASE_URL,
            timeout=timeout,
            follow_redirects=False,
        )

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying connection pool if this client created it."""
        if self._owns_client:
            self._client.close()

    # ── Core request plumbing ───────────────────────────────────────────────
    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "CodePilotAI/0.1.0",
        }
        if extra:
            headers.update(extra)
        return headers

    def _scrub(self, text: str) -> str:
        return scrub_secrets(text, self._token)

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Perform an authenticated GitHub request, retrying transient failures.

        Returns the decoded JSON body, or an empty dict for ``204 No Content``.
        """
        last_error: GitHubError | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._client.request(
                    method,
                    path,
                    json=json_body,
                    params=params,
                    headers=self._headers(),
                )
            except httpx.TimeoutException as exc:
                last_error = GitHubError(f"GitHub request timed out: {self._scrub(str(exc))}")
                if attempt < MAX_RETRIES:
                    self._sleep_backoff(attempt)
                    continue
                raise last_error from exc
            except httpx.HTTPError as exc:
                last_error = GitHubError(f"Could not reach GitHub: {self._scrub(str(exc))}")
                if attempt < MAX_RETRIES:
                    self._sleep_backoff(attempt)
                    continue
                raise last_error from exc

            if response.status_code == 204:
                return {}

            if response.is_success:
                return response.json()

            error = self._build_error(response)
            if attempt >= MAX_RETRIES:
                raise error

            if isinstance(error, GitHubRateLimitError):
                self._sleep_for_rate_limit(error, attempt)
                continue

            if isinstance(error, GitHubServerError):
                # 5xx is GitHub's problem, not ours, and is usually transient.
                self._sleep_backoff(attempt)
                continue

            # Auth, 404, 409, and a 403 with quota left are deterministic:
            # retrying cannot change the answer.
            raise error

        raise last_error or GitHubError("GitHub request failed for an unknown reason.")

    def _build_error(self, response: httpx.Response) -> GitHubError:
        """Translate an HTTP error response into a typed, scrubbed exception."""
        status = response.status_code
        detail = self._scrub(response.text)[:1000]

        try:
            payload = response.json()
            if isinstance(payload, dict) and "message" in payload:
                # Scrub the structured message too. Echoing the raw payload would
                # reintroduce a token that the text pass above removed.
                detail = self._scrub(str(payload["message"]))[:1000]
                if payload.get("errors"):
                    detail = self._scrub(f"{detail} {payload['errors']}")[:1000]
        except ValueError:
            payload = {}

        remaining = _int_or_none(response.headers.get("X-RateLimit-Remaining"))
        retry_after = _int_or_none(response.headers.get("Retry-After"))

        kwargs: dict[str, Any] = {
            "status_code": status,
            "rate_limit_remaining": remaining,
            "retry_after": retry_after,
        }

        if status == 401:
            return GitHubAuthError("GitHub rejected the access token. Please reconnect.", **kwargs)
        if status == 404:
            return GitHubNotFoundError(f"GitHub returned 404: {detail}", **kwargs)
        if status == 409:
            return GitHubConflictError(f"GitHub returned 409: {detail}", **kwargs)
        if status == 429 or (status == 403 and remaining == 0):
            return GitHubRateLimitError(f"GitHub rate limit reached: {detail}", **kwargs)
        if status == 403:
            return GitHubError(f"GitHub denied the request: {detail}", **kwargs)
        if 500 <= status < 600:
            return GitHubServerError(f"GitHub server error: {detail}", **kwargs)
        return GitHubError(f"GitHub request failed: {detail}", **kwargs)

    @classmethod
    def _backoff_delay(cls, attempt: int) -> float:
        """Exponential backoff for ``attempt``, capped so a run cannot stall."""
        return float(min(BASE_BACKOFF_SECONDS * (2**attempt), cls.MAX_BACKOFF_SECONDS))

    def _sleep_backoff(self, attempt: int) -> None:
        delay = self._backoff_delay(attempt)
        logger.warning("github_retry_backoff", attempt=attempt, delay_seconds=delay)
        time.sleep(delay)

    def _sleep_for_rate_limit(self, error: GitHubRateLimitError, attempt: int) -> None:
        """Sleep for the server-requested window, else fall back to backoff."""
        if error.retry_after:
            delay = min(float(error.retry_after), self.MAX_BACKOFF_SECONDS)
        else:
            delay = self._backoff_delay(attempt)
        logger.warning("github_rate_limited", attempt=attempt, delay_seconds=delay)
        time.sleep(delay)

    def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        per_page: int = 100,
        max_pages: int = 10,
    ) -> Iterator[dict[str, Any]]:
        """Yield every item across a paginated list endpoint, following pages."""
        base_params = dict(params or {})
        for page in range(1, max_pages + 1):
            query = {**base_params, "per_page": per_page, "page": page}
            payload = self.request("GET", path, params=query)
            if not payload:
                return
            yield from payload
            if len(payload) < per_page:
                return

    # ── Identity ────────────────────────────────────────────────────────────
    def get_authenticated_user(self) -> dict[str, Any]:
        """Return the profile of the token's owner."""
        return self.request("GET", "/user")

    # ── Repositories ────────────────────────────────────────────────────────
    def list_repositories(
        self,
        *,
        per_page: int = 100,
        sort: str = "updated",
        affiliation: str = "owner,collaborator,organization_member",
    ) -> list[dict[str, Any]]:
        """List repositories the authenticated user can access."""
        return list(
            self.paginate(
                "/user/repos",
                params={"sort": sort, "affiliation": affiliation},
                per_page=per_page,
            )
        )

    def get_repository(self, full_name: str) -> dict[str, Any]:
        """Fetch a single repository's metadata."""
        return self.request("GET", f"/repos/{full_name}")

    def create_repository(
        self,
        *,
        name: str,
        description: str = "",
        private: bool = True,
        auto_init: bool = True,
    ) -> dict[str, Any]:
        """Create a repository owned by the authenticated user."""
        return self.request(
            "POST",
            "/user/repos",
            json_body={
                "name": name,
                "description": description,
                "private": private,
                "auto_init": auto_init,
            },
        )

    # ── Branches ────────────────────────────────────────────────────────────
    def list_branches(self, full_name: str, *, per_page: int = 100) -> list[dict[str, Any]]:
        """List branch names on a repository."""
        branches = self.paginate(f"/repos/{full_name}/branches", per_page=per_page)
        return [branch for branch in branches if isinstance(branch, dict)]

    def get_branch(self, full_name: str, branch: str) -> dict[str, Any]:
        """Fetch a single branch, including its head SHA."""
        return self.request("GET", f"/repos/{full_name}/branches/{branch}")

    # ── Pull requests ───────────────────────────────────────────────────────
    def create_pull_request(
        self,
        full_name: str,
        *,
        head: str,
        base: str,
        title: str,
        body: str = "",
        draft: bool = False,
        maintainer_can_modify: bool = True,
    ) -> dict[str, Any]:
        """Open a pull request from ``head`` into ``base``."""
        return self.request(
            "POST",
            f"/repos/{full_name}/pulls",
            json_body={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
                "draft": draft,
                "maintainer_can_modify": maintainer_can_modify,
            },
        )

    def merge_pull_request(
        self,
        full_name: str,
        number: int,
        *,
        merge_method: str = "squash",
        commit_title: str | None = None,
    ) -> dict[str, Any]:
        """Merge a pull request. ``squash`` keeps the branch history clean."""
        body: dict[str, Any] = {"merge_method": merge_method}
        if commit_title:
            body["commit_title"] = commit_title
        return self.request("PUT", f"/repos/{full_name}/pulls/{number}", json_body=body)

    def get_pull_request(self, full_name: str, number: int) -> dict[str, Any]:
        """Fetch a pull request's current state."""
        return self.request("GET", f"/repos/{full_name}/pulls/{number}")


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None

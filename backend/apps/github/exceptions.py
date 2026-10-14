"""Exceptions raised by the GitHub integration.

Every exception message is scrubbed of credentials before it is constructed, so
a traceback can never leak an access token to logs or to the mobile client.
"""

from __future__ import annotations


class GitHubError(Exception):
    """Base class for all GitHub integration failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        rate_limit_remaining: int | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.rate_limit_remaining = rate_limit_remaining
        self.retry_after = retry_after

    def __str__(self) -> str:
        if self.status_code:
            return f"{self.message} (HTTP {self.status_code})"
        return self.message


class GitHubAuthError(GitHubError):
    """Authentication failed, or the stored token is invalid or revoked."""


class GitHubNotFoundError(GitHubError):
    """The requested repository, branch, or pull request does not exist."""


class GitHubRateLimitError(GitHubError):
    """Primary or secondary rate limit hit."""


class GitHubConflictError(GitHubError):
    """The resource already exists (e.g. creating a duplicate repository)."""


class GitHubServerError(GitHubError):
    """GitHub returned 5xx. Transient, so the client retries it."""


__all__: list[str] = [
    "GitHubAuthError",
    "GitHubConflictError",
    "GitHubError",
    "GitHubNotFoundError",
    "GitHubRateLimitError",
    "GitHubServerError",
]

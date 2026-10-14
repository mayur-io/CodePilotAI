"""Domain services for the repositories app.

Kept separate from views so the same logic serves the API, Celery tasks, and
management commands.
"""

from __future__ import annotations

from typing import Any

from apps.github.client import GitHubClient
from apps.repositories.models import Branch, Repository
from config.observability import get_logger

logger = get_logger(__name__)


class GitHubNotConnectedError(Exception):
    """Raised when an operation needs a GitHub token but the user has none."""


def github_client_for(user: Any) -> GitHubClient:
    """Build a :class:`GitHubClient` for ``user``, or raise if not connected."""
    token = user.github_access_token
    if not token:
        raise GitHubNotConnectedError("Connect your GitHub account first.")
    return GitHubClient(token)


def upsert_repository(user: Any, payload: dict[str, Any]) -> Repository:
    """Create or refresh the local :class:`Repository` from a GitHub payload.

    ``github_id`` is the identity key so a repo rename or transfer does not
    create a duplicate row.
    """
    github_id = int(payload["id"])
    repo, _ = Repository.objects.update_or_create(
        user=user,
        github_id=github_id,
        defaults={
            "name": payload.get("name") or "",
            "full_name": payload.get("full_name") or "",
            "description": payload.get("description") or "",
            "html_url": payload.get("html_url") or "",
            "default_branch": payload.get("default_branch") or "main",
            "primary_language": payload.get("language") or "",
            "topics": payload.get("topics") or [],
            "is_private": bool(payload.get("private", False)),
            "is_fork": bool(payload.get("fork", False)),
            "is_archived": bool(payload.get("archived", False)),
            "size_kb": int(payload.get("size") or 0),
        },
    )
    return repo


def sync_repositories_from_github(user: Any, *, limit_pages: int = 3) -> int:
    """Refresh the user's repository list from GitHub.

    Returns the number of repositories synced.
    """
    with github_client_for(user) as client:
        repos: list[dict[str, Any]] = []
        for page in range(1, limit_pages + 1):
            batch = client.request(
                "GET",
                "/user/repos",
                params={"per_page": 100, "page": page, "sort": "updated"},
            )
            if not batch:
                break
            repos.extend(batch)
            if len(batch) < 100:
                break

    for payload in repos:
        upsert_repository(user, payload)

    logger.info("repositories_synced", user_id=user.pk, count=len(repos))
    return len(repos)


def sync_default_branch(user: Any, repository: Repository) -> str:
    """Ensure the default branch row exists and its metadata is current."""
    with github_client_for(user) as client:
        payload = client.get_repository(repository.full_name)

    default_branch = payload.get("default_branch") or repository.default_branch
    repository.default_branch = default_branch
    repository.size_kb = int(payload.get("size") or repository.size_kb)
    repository.save(update_fields=["default_branch", "size_kb", "updated_at"])

    Branch.objects.update_or_create(
        repository=repository,
        name=default_branch,
        defaults={"kind": Branch.Kind.DEFAULT},
    )
    return default_branch


def list_branches(user: Any, repository: Repository) -> list[dict[str, Any]]:
    """Fetch branch names from GitHub and mirror them locally."""
    with github_client_for(user) as client:
        remote = client.list_branches(repository.full_name)

    known: list[dict[str, Any]] = []
    for entry in remote:
        name = entry.get("name")
        if not name:
            continue
        head_sha = ((entry.get("commit") or {}).get("sha")) or ""
        is_default = name == repository.default_branch
        Branch.objects.update_or_create(
            repository=repository,
            name=name,
            defaults={
                "head_sha": head_sha,
                "kind": Branch.Kind.DEFAULT if is_default else Branch.Kind.FEATURE,
            },
        )
        known.append({"name": name, "head_sha": head_sha})

    return known


def create_repository(user: Any, payload: dict[str, Any]) -> Repository:
    """Create a repository on GitHub and record it locally."""
    with github_client_for(user) as client:
        created = client.create_repository(
            name=payload["name"],
            description=payload.get("description", ""),
            private=payload.get("private", True),
            auto_init=payload.get("auto_init", True),
        )
    logger.info("repository_created", user_id=user.pk, full_name=created.get("full_name"))
    return upsert_repository(user, created)


def require_connected(user: Any) -> None:
    """Raise a typed error if the user has no usable GitHub token."""
    token = user.github_access_token
    if not token:
        raise GitHubNotConnectedError("Connect your GitHub account first.")


__all__ = [
    "GitHubNotConnectedError",
    "create_repository",
    "github_client_for",
    "list_branches",
    "require_connected",
    "sync_default_branch",
    "sync_repositories_from_github",
    "upsert_repository",
]

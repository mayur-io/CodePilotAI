"""Repository and branch models.

A :class:`Repository` is the local record of a GitHub repo the user has chosen to
work in. ``mirror_path`` points at a bare ``git clone --mirror`` kept on the
server so agent runs can create worktrees without re-downloading history.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class Repository(models.Model):
    """A GitHub repository the user has connected to CodePilotAI."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="repositories",
        help_text="Owner of this connection. Every lookup is scoped to this user.",
    )

    # ── GitHub identity ─────────────────────────────────────────────────────
    github_id = models.BigIntegerField(
        help_text="GitHub numeric repo id. Stable across renames and transfers.",
    )
    name = models.CharField(max_length=255)
    full_name = models.CharField(max_length=520, help_text="e.g. octocat/hello-world")
    description = models.TextField(blank=True, default="")
    html_url = models.URLField(max_length=500, blank=True, default="")
    default_branch = models.CharField(max_length=255, default="main")
    primary_language = models.CharField(max_length=100, blank=True, default="")
    topics = models.JSONField(default=list, blank=True)

    is_private = models.BooleanField(default=False)
    is_fork = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)
    size_kb = models.PositiveIntegerField(default=0, help_text="GitHub-reported size in KB.")

    # ── Local mirror state ──────────────────────────────────────────────────
    mirror_path = models.CharField(
        max_length=1000,
        blank=True,
        default="",
        help_text="Absolute path to the bare mirror on this server. Empty until cloned.",
    )
    mirror_synced_at = models.DateTimeField(null=True, blank=True)
    clone_failed_at = models.DateTimeField(null=True, blank=True)
    clone_error = models.TextField(blank=True, default="")

    # ── Usage bookkeeping ───────────────────────────────────────────────────
    last_opened_at = models.DateTimeField(default=timezone.now)
    task_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "repositories_repository"
        ordering = ("-last_opened_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("user", "github_id"),
                name="uniq_repo_per_user",
            )
        ]
        indexes = [
            models.Index(fields=("user", "-last_opened_at"), name="repo_user_opened_idx"),
            models.Index(fields=("user", "full_name"), name="repo_user_fullname_idx"),
        ]
        verbose_name = "repository"
        verbose_name_plural = "repositories"

    def __str__(self) -> str:
        return self.full_name

    @property
    def clone_url(self) -> str:
        """HTTPS clone URL. Auth is injected at push time, never stored here."""
        return f"https://github.com/{self.full_name}.git"

    @property
    def is_cloned(self) -> bool:
        """True when a usable local mirror exists."""
        return bool(self.mirror_path)

    @property
    def needs_sync(self) -> bool:
        """True when the mirror is stale enough to warrant a background fetch."""
        if self.mirror_synced_at is None:
            return True
        return (timezone.now() - self.mirror_synced_at).total_seconds() > 300


class Branch(models.Model):
    """A branch on a repository, usually a CodePilotAI feature branch."""

    class Kind(models.TextChoices):
        DEFAULT = "default", "Default branch"
        FEATURE = "feature", "Agent feature branch"

    repository = models.ForeignKey(
        Repository,
        on_delete=models.CASCADE,
        related_name="branches",
    )
    name = models.CharField(max_length=255)
    head_sha = models.CharField(max_length=64, blank=True, default="")
    kind = models.CharField(
        max_length=16,
        choices=Kind.choices,
        default=Kind.FEATURE,
    )
    is_open = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Fields populated when the branch has an associated pull request.
    pr_number = models.PositiveIntegerField(null=True, blank=True)
    pr_url = models.URLField(max_length=500, blank=True, default="")
    pr_state = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        db_table = "repositories_branch"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("repository", "name"),
                name="uniq_branch_per_repo",
            )
        ]
        indexes = [
            models.Index(fields=("repository", "-created_at"), name="branch_repo_created_idx"),
        ]
        verbose_name = "branch"
        verbose_name_plural = "branches"

    def __str__(self) -> str:
        return f"{self.repository.full_name}@{self.name}"

    @property
    def is_merged(self) -> bool:
        """True when the associated PR was merged."""
        return self.pr_state.lower() == "merged"

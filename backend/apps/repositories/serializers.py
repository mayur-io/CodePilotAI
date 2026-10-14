"""Serializers for the repositories API."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.repositories.models import Branch, Repository


class BranchSerializer(serializers.ModelSerializer[Branch]):
    """A repository branch, including its pull-request coordinates."""

    is_merged = serializers.BooleanField(read_only=True)

    class Meta:
        model = Branch
        fields = (
            "id",
            "name",
            "head_sha",
            "kind",
            "is_open",
            "pr_number",
            "pr_url",
            "pr_state",
            "is_merged",
            "created_at",
        )
        read_only_fields = fields


class RepositorySerializer(serializers.ModelSerializer[Repository]):
    """A connected repository as shown in the repo picker."""

    is_cloned = serializers.BooleanField(read_only=True)
    clone_url = serializers.CharField(read_only=True)
    needs_sync = serializers.BooleanField(read_only=True)

    class Meta:
        model = Repository
        fields = (
            "id",
            "github_id",
            "name",
            "full_name",
            "description",
            "html_url",
            "default_branch",
            "primary_language",
            "topics",
            "is_private",
            "is_fork",
            "is_archived",
            "size_kb",
            "is_cloned",
            "clone_url",
            "needs_sync",
            "task_count",
            "last_opened_at",
            "created_at",
        )
        read_only_fields = fields


class RepositoryCreateSerializer(serializers.Serializer[dict[str, Any]]):
    """Request body for creating a repository on GitHub.

    Validated locally before any GitHub call so obvious mistakes fail fast and
    cheaply.
    """

    name = serializers.CharField(
        max_length=100,
        help_text="Repository name. GitHub allows letters, digits, ., -, and _.",
    )
    description = serializers.CharField(
        max_length=350,
        required=False,
        allow_blank=True,
        default="",
    )
    private = serializers.BooleanField(default=True)
    auto_init = serializers.BooleanField(
        default=True,
        help_text="Create an initial commit so the repo has a default branch.",
    )

    def validate_name(self, value: str) -> str:
        """Reject names GitHub will not accept, before spending an API call."""
        cleaned = value.strip()
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
        invalid = sorted(set(cleaned) - allowed)
        if invalid:
            raise serializers.ValidationError(
                f"Repository name contains unsupported characters: {' '.join(invalid)}"
            )
        return cleaned


class RepositorySyncSerializer(serializers.Serializer[dict[str, Any]]):
    """Result of refreshing a repository's local mirror."""

    detail = serializers.CharField()
    repository_id = serializers.IntegerField()
    cloned = serializers.BooleanField()
    head_sha = serializers.CharField(allow_blank=True, default="")


__all__: list[str] = [
    "BranchSerializer",
    "RepositoryCreateSerializer",
    "RepositorySerializer",
    "RepositorySyncSerializer",
]

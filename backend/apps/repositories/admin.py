"""Admin views for repositories and branches."""

from __future__ import annotations

from typing import Any

from django.contrib import admin

from apps.repositories.models import Branch, Repository


@admin.register(Repository)
class RepositoryAdmin(admin.ModelAdmin):
    """Read-oriented admin: connection metadata and mirror health."""

    list_display = (
        "full_name",
        "user",
        "default_branch",
        "is_private",
        "is_cloned",
        "mirror_synced_at",
        "task_count",
    )
    list_filter = ("is_private", "is_fork", "is_archived")
    search_fields = ("full_name", "name", "description")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("Identity", {"fields": ("user", "github_id", "name", "full_name", "html_url")}),
        ("Git", {"fields": ("default_branch", "primary_language", "topics", "size_kb")}),
        ("Flags", {"fields": ("is_private", "is_fork", "is_archived")}),
        (
            "Mirror",
            {
                "fields": (
                    "mirror_path",
                    "mirror_synced_at",
                    "clone_failed_at",
                    "clone_error",
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at", "last_opened_at")}),
    )

    def get_queryset(self, request: Any) -> Any:
        """Superusers see everything; staff see only their own connections."""
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        return queryset.filter(user=request.user)


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    """Admin view of agent-created branches and their pull requests."""

    list_display = ("name", "repository", "kind", "head_sha", "pr_number", "pr_state", "created_at")
    list_filter = ("kind", "is_open")
    search_fields = ("name", "repository__full_name")
    autocomplete_fields = ("repository",)
    readonly_fields = ("created_at", "updated_at")

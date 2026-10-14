"""Admin views for agent tasks and approvals."""

from __future__ import annotations

from typing import Any

from django.contrib import admin

from apps.agent_tasks.models import AgentTask, Message, ProposedChange, RunEvent


class ProposedChangeInline(admin.TabularInline):
    """Proposed changes shown inline on the task."""

    model = ProposedChange
    extra = 0
    fields = ("order_index", "path", "change_type", "additions", "deletions", "status")
    readonly_fields = fields
    ordering = ("order_index", "id")
    can_delete = False

    def has_add_permission(self, request: Any, obj: Any = None) -> bool:
        """Changes are produced by the agent, never typed in by hand."""
        return False


@admin.register(AgentTask)
class AgentTaskAdmin(admin.ModelAdmin):
    """Read-mostly admin for diagnosing agent runs."""

    list_display = (
        "id",
        "title",
        "repository",
        "status",
        "phase",
        "feature_branch",
        "total_tokens",
        "cost_usd",
        "created_at",
    )
    list_filter = ("status", "phase")
    search_fields = ("title", "prompt", "feature_branch", "repository__full_name")
    autocomplete_fields = ("user", "repository")
    readonly_fields = (
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "agent_reasoning",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cached_tokens",
        "cost_usd",
        "llm_calls",
        "tool_calls",
    )
    inlines = (ProposedChangeInline,)
    fieldsets = (
        ("Task", {"fields": ("user", "repository", "title", "prompt", "followup_count")}),
        ("Git", {"fields": ("base_branch", "feature_branch", "base_sha", "commit_sha", "pr_url")}),
        ("State", {"fields": ("status", "phase", "error_code", "error_message")}),
        ("Output", {"fields": ("agent_summary", "detected_stack", "diff_stat", "verification")}),
        (
            "Usage",
            {
                "fields": (
                    "input_tokens",
                    "output_tokens",
                    "reasoning_tokens",
                    "cached_tokens",
                    "cost_usd",
                )
            },
        ),
    )

    def get_queryset(self, request: Any) -> Any:
        """Superusers see everything; staff see only their own tasks."""
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        return queryset.filter(user=request.user)

    def has_add_permission(self, request: Any) -> bool:
        """Tasks are created through the API, not the admin."""
        return False


@admin.register(ProposedChange)
class ProposedChangeAdmin(admin.ModelAdmin):
    """Inspect the exact diff the agent proposed and how it was decided."""

    list_display = ("id", "task", "path", "change_type", "additions", "deletions", "status")
    list_filter = ("status", "change_type")
    search_fields = ("path", "rationale")
    readonly_fields = ("created_at", "updated_at", "decided_at")
    fieldsets = (
        ("Change", {"fields": ("task", "path", "previous_path", "change_type", "order_index")}),
        ("Diff", {"fields": ("patch", "additions", "deletions")}),
        (
            "Decision",
            {
                "fields": (
                    "status",
                    "rationale",
                    "user_feedback",
                    "error_message",
                    "decided_at",
                )
            },
        ),
    )


@admin.register(RunEvent)
class RunEventAdmin(admin.ModelAdmin):
    """Raw event stream for a task."""

    list_display = ("task", "seq", "kind", "created_at")
    list_filter = ("kind",)
    search_fields = ("task__title",)
    readonly_fields = ("created_at",)

    def has_add_permission(self, request: Any) -> bool:
        return False

    def has_change_permission(self, request: Any, obj: Any = None) -> bool:
        return False


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    """Thread messages for a task."""

    list_display = ("task", "role", "created_at")
    list_filter = ("role",)
    search_fields = ("content", "task__title")
    readonly_fields = ("created_at",)

    def has_add_permission(self, request: Any) -> bool:
        return False

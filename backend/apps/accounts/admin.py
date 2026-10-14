"""Django admin registration for user accounts."""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from apps.accounts.models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Admin view of GitHub-identity users.

    Encrypted token columns are deliberately excluded from the list view so they
    cannot leak into a screenshot or an export.
    """

    ordering = ("-date_joined",)
    list_display = (
        "username",
        "email",
        "github_id",
        "is_github_connected",
        "is_staff",
        "date_joined",
    )
    list_filter = ("is_staff", "is_superuser", "is_active")
    search_fields = ("username", "email", "github_id")
    readonly_fields = ("date_joined", "github_connected_at", "last_login")
    exclude = ("password",)

    fieldsets = (
        (None, {"fields": ("username", "email", "full_name")}),
        ("GitHub", {"fields": ("github_id", "avatar_url", "github_connected_at")}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )

    def get_queryset(self, request: Any) -> Any:
        """Never load token ciphertext into admin lists."""
        queryset = super().get_queryset(request)
        return queryset.defer("access_token_enc", "refresh_token_enc")

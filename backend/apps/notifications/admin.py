"""Admin views for registered push devices."""

from __future__ import annotations

from typing import Any

from django.contrib import admin

from apps.notifications.models import DeviceToken


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    """Inspect and prune device registrations.

    Tokens are written by the client through the API; the admin only looks at
    them (and can delete a dead install).
    """

    list_display = ("id", "user", "platform", "token_preview", "last_seen_at", "created_at")
    list_filter = ("platform",)
    search_fields = ("user__username", "token")
    autocomplete_fields = ("user",)
    readonly_fields = ("token", "platform", "created_at", "updated_at", "last_seen_at")

    @admin.display(description="Token")
    def token_preview(self, obj: DeviceToken) -> str:
        """Show just enough of the token to identify a device, never all of it."""
        return f"{obj.token[:16]}…" if len(obj.token) > 16 else obj.token

    def has_add_permission(self, request: Any) -> bool:
        """Devices register through the API, not the admin."""
        return False


__all__: list[str] = ["DeviceTokenAdmin"]

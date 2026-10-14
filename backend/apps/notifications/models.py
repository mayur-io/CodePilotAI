"""FCM device registrations.

One row per app install. The Firebase registration token is the natural key: it
is unique per install, rotates underneath us, and the client re-registers it on
every launch, so registration has to be an upsert rather than an insert.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models
from django.utils import timezone


class DeviceToken(models.Model):
    """A push-notification registration token belonging to a user's device."""

    class Platform(models.TextChoices):
        ANDROID = "android", "Android"
        IOS = "ios", "iOS"
        WEB = "web", "Web"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="device_tokens",
    )
    token = models.CharField(
        max_length=4096,
        unique=True,
        help_text="FCM registration token. Rotates; the client re-registers on each launch.",
    )
    platform = models.CharField(
        max_length=16,
        choices=Platform.choices,
        default=Platform.ANDROID,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_seen_at = models.DateTimeField(
        default=timezone.now,
        help_text="When the client last confirmed this token, used to age out dead installs.",
    )

    class Meta:
        db_table = "notifications_device_token"
        ordering = ("-last_seen_at",)
        indexes = [
            models.Index(fields=("user", "platform"), name="device_user_platform_idx"),
        ]
        verbose_name = "device token"
        verbose_name_plural = "device tokens"

    def __str__(self) -> str:
        return f"{self.platform}:{self.user_id}:{self.token[:12]}…"

    @classmethod
    def register(
        cls,
        user: Any,
        *,
        token: str,
        platform: str = Platform.ANDROID,
    ) -> tuple[DeviceToken, bool]:
        """Upsert a token, reassigning it to ``user`` if the device changed accounts.

        Returns ``(device, created)`` so the view can answer 201 vs 200 without a
        second query.
        """
        token = token.strip()
        device, created = cls.objects.update_or_create(
            token=token,
            defaults={"user": user, "platform": platform, "last_seen_at": timezone.now()},
        )
        return device, created


__all__: list[str] = ["DeviceToken"]

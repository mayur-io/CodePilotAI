"""Serializers for device registration."""

from __future__ import annotations

from typing import Any, ClassVar

from rest_framework import serializers

from apps.notifications.models import DeviceToken


class DeviceTokenSerializer(serializers.ModelSerializer[DeviceToken]):
    """Request/response body for registering a device.

    ``token`` is the FCM registration token the client obtained from Firebase;
    the server never mints it.
    """

    class Meta:
        model = DeviceToken
        fields = ("id", "token", "platform", "created_at", "updated_at", "last_seen_at")
        read_only_fields = ("id", "created_at", "updated_at", "last_seen_at")
        # Registration is an upsert, so the model's unique-token validator must
        # not run here -- a repeat token is the expected, idempotent case.
        extra_kwargs: ClassVar[dict[str, Any]] = {"token": {"validators": []}}

    def validate_token(self, value: str) -> str:
        """Reject a blank token after trimming; FCM tokens have no spaces."""
        token = value.strip()
        if not token:
            raise serializers.ValidationError("A registration token is required.")
        return token


class DeviceUnregisterSerializer(serializers.Serializer[dict[str, str]]):
    """Request body for dropping a device registration."""

    token = serializers.CharField(max_length=4096, trim_whitespace=True)


__all__: list[str] = ["DeviceTokenSerializer", "DeviceUnregisterSerializer"]

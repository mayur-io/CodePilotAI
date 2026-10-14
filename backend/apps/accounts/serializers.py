"""Serializers for the accounts API."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from rest_framework import serializers

User = get_user_model()


class UserSerializer(serializers.ModelSerializer[User]):
    """Public representation of the signed-in user.

    Deliberately omits every token field: the client has no reason to hold, and
    no permission, to read GitHub credentials.
    """

    display_name = serializers.CharField(read_only=True)
    is_github_connected = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "display_name",
            "email",
            "full_name",
            "avatar_url",
            "is_github_connected",
            "is_staff",
            "date_joined",
        )
        read_only_fields = fields


class AuthorizeURLSerializer(serializers.Serializer[str]):
    """Response body for ``GET /auth/github/start``."""

    authorize_url = serializers.URLField()
    expires_in = serializers.IntegerField(help_text="Seconds until the state token expires.")


class HandoffExchangeSerializer(serializers.Serializer[dict[str, Any]]):
    """Request body for exchanging a one-time OAuth code for JWTs."""

    code = serializers.CharField(min_length=16, max_length=128, write_only=True)


class TokenPairSerializer(serializers.Serializer[dict[str, Any]]):
    """The JWT pair plus the user profile, returned after a successful exchange."""

    access = serializers.CharField()
    refresh = serializers.CharField()
    user = UserSerializer()


class LogoutSerializer(serializers.Serializer[dict[str, str]]):
    """Request body for logout. ``refresh`` is optional."""

    refresh = serializers.CharField(required=False, allow_blank=True, default="")


class LogoutResponseSerializer(serializers.Serializer[dict[str, Any]]):
    """Result of a logout call."""

    detail = serializers.CharField()
    revoked = serializers.BooleanField()


__all__: list[str] = [
    "AuthorizeURLSerializer",
    "HandoffExchangeSerializer",
    "LogoutResponseSerializer",
    "LogoutSerializer",
    "TokenPairSerializer",
    "UserSerializer",
]

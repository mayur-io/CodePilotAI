"""API views for push-notification device registration.

The client registers its FCM token after sign-in (and whenever Firebase rotates
it), and drops it on sign-out so the next user of the device does not inherit
the previous user's notifications.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notifications.models import DeviceToken
from apps.notifications.serializers import (
    DeviceTokenSerializer,
    DeviceUnregisterSerializer,
)
from config.observability import get_logger

logger = get_logger(__name__)


class DeviceRegisterView(APIView):
    """Register (or refresh) the calling device's FCM token."""

    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="Register a device",
        description=(
            "Idempotent upsert of an FCM registration token. Re-registering the "
            "same token updates its ``last_seen_at`` and returns 200; a new token "
            "returns 201. A token that already belongs to another account is "
            "reassigned, because a device has exactly one signed-in user."
        ),
        request=DeviceTokenSerializer,
        responses={200: DeviceTokenSerializer, 201: DeviceTokenSerializer},
    )
    def post(self, request: Any) -> Response:
        serializer = DeviceTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        device, created = DeviceToken.register(
            request.user,
            token=serializer.validated_data["token"],
            platform=serializer.validated_data.get("platform") or DeviceToken.Platform.ANDROID,
        )
        logger.info(
            "device_registered",
            user_id=request.user.pk,
            device_id=device.pk,
            platform=device.platform,
            created=created,
        )
        return Response(
            DeviceTokenSerializer(device).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class DeviceUnregisterView(APIView):
    """Forget a device token, scoped to the calling user."""

    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="Unregister a device",
        description="Drop one of the caller's own device tokens. Unknown tokens are ignored.",
        request=DeviceUnregisterSerializer,
        responses={204: None},
    )
    def post(self, request: Any) -> Response:
        serializer = DeviceUnregisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        DeviceToken.objects.filter(
            user=request.user,
            token=serializer.validated_data["token"],
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


__all__: list[str] = ["DeviceRegisterView", "DeviceUnregisterView"]

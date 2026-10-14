"""API tests for push-notification device registration."""

from __future__ import annotations

from typing import Any

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.notifications.models import DeviceToken

pytestmark = pytest.mark.django_db


def register_url() -> str:
    return reverse("v1:device-register")


def unregister_url() -> str:
    return reverse("v1:device-unregister")


class TestDeviceRegistration:
    """`POST /api/v1/devices` is an idempotent upsert scoped to the caller."""

    def test_register_creates_device(self, auth_client: APIClient, user: Any) -> None:
        response = auth_client.post(
            register_url(),
            {"token": "fcm-token-alice", "platform": "android"},
            format="json",
        )

        assert response.status_code == 201
        assert response.data["token"] == "fcm-token-alice"
        assert response.data["platform"] == "android"
        device = DeviceToken.objects.get(token="fcm-token-alice")
        assert device.user_id == user.pk

    def test_register_is_idempotent(self, auth_client: APIClient, user: Any) -> None:
        auth_client.post(register_url(), {"token": "fcm-token-alice"}, format="json")
        response = auth_client.post(register_url(), {"token": "fcm-token-alice"}, format="json")

        assert response.status_code == 200
        assert DeviceToken.objects.filter(user=user).count() == 1

    def test_register_reassigns_a_token_that_changed_owner(
        self, auth_client: APIClient, user: Any, other_user: Any
    ) -> None:
        DeviceToken.objects.create(user=other_user, token="shared-device", platform="android")

        response = auth_client.post(register_url(), {"token": "shared-device"}, format="json")

        assert response.status_code == 200
        device = DeviceToken.objects.get(token="shared-device")
        assert device.user_id == user.pk
        assert not DeviceToken.objects.filter(user=other_user).exists()

    def test_platform_defaults_to_android(self, auth_client: APIClient, user: Any) -> None:
        response = auth_client.post(register_url(), {"token": "fcm-token-alice"}, format="json")

        assert response.status_code == 201
        assert DeviceToken.objects.get(user=user).platform == "android"

    def test_token_is_trimmed(self, auth_client: APIClient) -> None:
        response = auth_client.post(register_url(), {"token": "  spaced-token  "}, format="json")

        assert response.status_code == 201
        assert DeviceToken.objects.get().token == "spaced-token"

    def test_blank_token_is_rejected(self, auth_client: APIClient) -> None:
        response = auth_client.post(register_url(), {"token": "   "}, format="json")

        assert response.status_code == 400
        assert DeviceToken.objects.count() == 0

    def test_missing_token_is_rejected(self, auth_client: APIClient) -> None:
        response = auth_client.post(register_url(), {}, format="json")

        assert response.status_code == 400

    def test_unknown_platform_is_rejected(self, auth_client: APIClient) -> None:
        response = auth_client.post(
            register_url(), {"token": "t", "platform": "blackberry"}, format="json"
        )

        assert response.status_code == 400

    def test_requires_authentication(self, api_client: APIClient) -> None:
        response = api_client.post(register_url(), {"token": "t"}, format="json")

        assert response.status_code == 401
        assert DeviceToken.objects.count() == 0


class TestDeviceUnregistration:
    """`POST /api/v1/devices/unregister` deletes only the caller's own token."""

    def test_unregister_removes_the_device(self, auth_client: APIClient, user: Any) -> None:
        DeviceToken.objects.create(user=user, token="fcm-token-alice")

        response = auth_client.post(unregister_url(), {"token": "fcm-token-alice"}, format="json")

        assert response.status_code == 204
        assert DeviceToken.objects.filter(user=user).count() == 0

    def test_unregister_cannot_touch_another_users_device(
        self, auth_client: APIClient, other_user: Any
    ) -> None:
        DeviceToken.objects.create(user=other_user, token="bob-device")

        response = auth_client.post(unregister_url(), {"token": "bob-device"}, format="json")

        assert response.status_code == 204
        assert DeviceToken.objects.filter(user=other_user, token="bob-device").exists()

    def test_unregister_unknown_token_is_a_no_op(self, auth_client: APIClient) -> None:
        response = auth_client.post(unregister_url(), {"token": "never-seen"}, format="json")

        assert response.status_code == 204

    def test_unregister_requires_authentication(self, api_client: APIClient) -> None:
        response = api_client.post(unregister_url(), {"token": "t"}, format="json")

        assert response.status_code == 401

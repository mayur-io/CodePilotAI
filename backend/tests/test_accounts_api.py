"""Tests for the accounts API: OAuth start, callback, handoff exchange, logout.

The callback is reached by a browser, not by the app, so its failures are
reported as deep-link redirects carrying an error code rather than HTTP status
codes. Several tests assert that shape.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from django.urls import reverse
from django.utils import timezone
from pytest_httpx import HTTPXMock
from rest_framework.test import APIClient
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import OAuthHandoff, User
from apps.agent_tasks.models import AgentTask
from apps.github.oauth import generate_state

pytestmark = pytest.mark.django_db

TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"

GITHUB_PROFILE = {
    "id": 4242,
    "login": "carol",
    "name": "Carol Danvers",
    "email": "carol@example.com",
    "avatar_url": "https://avatars.githubusercontent.com/u/4242",
}


@pytest.fixture(autouse=True)
def github_oauth_configured(settings: Any) -> None:
    """Configure the OAuth app for this module.

    Test settings deliberately ship blank GitHub credentials so a missing `.env`
    cannot trigger a real call, and the authorize endpoint answers 503 when they
    are absent. The credentials here are placeholders; `httpx_mock` blocks every
    outbound request.
    """
    settings.GITHUB_CLIENT_ID = "test-client-id"
    settings.GITHUB_CLIENT_SECRET = "test-client-secret"
    settings.GITHUB_REDIRECT_URI = "http://testserver/api/v1/auth/github/callback"


def mock_oauth_exchange(httpx_mock: HTTPXMock, *, token: str = "gho_real") -> None:
    httpx_mock.add_response(
        url=TOKEN_URL,
        method="POST",
        json={"access_token": token, "refresh_token": "ghr_real", "scope": "repo,read:user"},
    )


def mock_profile(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=USER_URL, method="GET", json=GITHUB_PROFILE)


def redirect_url(response: Any) -> str:
    """Return the deep-link target of a 302.

    A DRF ``Response`` has no ``.url``; the target lives in the ``Location``
    header.
    """
    return response["Location"]


def redirect_params(response: Any) -> dict[str, list[str]]:
    """Parse the query string out of a deep-link redirect."""
    return parse_qs(urlparse(redirect_url(response)).query)


class TestAuthorizeStart:
    def test_returns_a_github_url_and_no_token(self, api_client: APIClient) -> None:
        response = api_client.get(reverse("v1:github-start"))

        assert response.status_code == 200, response.data
        assert response.data["authorize_url"].startswith(
            "https://github.com/login/oauth/authorize?"
        )
        assert "access" not in response.data
        assert "refresh" not in response.data

    def test_carries_a_signed_state(self, api_client: APIClient) -> None:
        response = api_client.get(reverse("v1:github-start"))

        state = parse_qs(urlparse(response.data["authorize_url"]).query)["state"][0]
        assert state

    def test_state_is_unique_per_request(self, api_client: APIClient) -> None:
        first = api_client.get(reverse("v1:github-start")).data["authorize_url"]
        second = api_client.get(reverse("v1:github-start")).data["authorize_url"]

        assert first != second

    def test_requests_the_repo_scope(self, api_client: APIClient) -> None:
        response = api_client.get(reverse("v1:github-start"))

        scopes = parse_qs(urlparse(response.data["authorize_url"]).query)["scope"][0].split()
        assert "repo" in scopes

    def test_unconfigured_server_says_so(self, api_client: APIClient, settings: Any) -> None:
        settings.GITHUB_CLIENT_ID = ""
        assert settings.ANDROID_DEEP_LINK_SCHEME == "codepilotai"

        response = api_client.get(reverse("v1:github-start"))

        assert response.status_code == 503
        assert response.data["error"]["code"] == "github_not_configured"


class TestCallbackSuccess:
    def test_redirects_to_the_app_with_a_handoff_code(
        self, api_client: APIClient, httpx_mock: HTTPXMock, settings: Any
    ) -> None:
        mock_oauth_exchange(httpx_mock)
        mock_profile(httpx_mock)

        response = api_client.get(
            reverse("v1:github-callback"), {"code": "auth-code", "state": generate_state()}
        )

        assert response.status_code == 302
        assert redirect_url(response).startswith("codepilotai://auth/callback?code=")

    def test_no_jwt_appears_in_the_redirect_url(
        self, api_client: APIClient, httpx_mock: HTTPXMock, settings: Any
    ) -> None:
        mock_oauth_exchange(httpx_mock)
        mock_profile(httpx_mock)

        response = api_client.get(
            reverse("v1:github-callback"), {"code": "auth-code", "state": generate_state()}
        )

        # Tokens in a redirect land in browser history, proxy logs, and referrers.
        assert "access" not in redirect_url(response)
        assert "gho_real" not in redirect_url(response)

    def test_creates_the_user_from_the_github_profile(
        self, api_client: APIClient, httpx_mock: HTTPXMock
    ) -> None:
        mock_oauth_exchange(httpx_mock)
        mock_profile(httpx_mock)

        api_client.get(
            reverse("v1:github-callback"), {"code": "auth-code", "state": generate_state()}
        )

        user = User.objects.get()
        assert user.username == "carol"
        assert user.github_id == 4242
        assert user.full_name == "Carol Danvers"
        assert user.is_github_connected is True

    def test_stores_the_token_encrypted_at_rest(
        self, api_client: APIClient, httpx_mock: HTTPXMock
    ) -> None:
        mock_oauth_exchange(httpx_mock)
        mock_profile(httpx_mock)

        api_client.get(
            reverse("v1:github-callback"), {"code": "auth-code", "state": generate_state()}
        )

        stored = User.objects.get()
        assert "gho_real" not in stored.access_token_enc
        assert stored.github_access_token == "gho_real"

    def test_the_account_has_no_usable_password(
        self, api_client: APIClient, httpx_mock: HTTPXMock
    ) -> None:
        # GitHub is the only identity provider, so password login must be closed.
        mock_oauth_exchange(httpx_mock)
        mock_profile(httpx_mock)

        api_client.get(
            reverse("v1:github-callback"), {"code": "auth-code", "state": generate_state()}
        )

        assert User.objects.get().has_usable_password() is False

    def test_reuses_an_existing_user_on_second_sign_in(
        self, api_client: APIClient, httpx_mock: HTTPXMock
    ) -> None:
        mock_oauth_exchange(httpx_mock)
        mock_profile(httpx_mock)
        api_client.get(reverse("v1:github-callback"), {"code": "c", "state": generate_state()})

        mock_oauth_exchange(httpx_mock, token="gho_second")
        mock_profile(httpx_mock)
        api_client.get(reverse("v1:github-callback"), {"code": "c", "state": generate_state()})

        assert User.objects.count() == 1
        assert User.objects.get().github_access_token == "gho_second"

    def test_follows_a_github_login_rename(
        self, api_client: APIClient, httpx_mock: HTTPXMock, connected_user: Any
    ) -> None:
        connected_user.github_id = 4242
        connected_user.username = "carol-old"
        connected_user.save()

        mock_oauth_exchange(httpx_mock)
        mock_profile(httpx_mock)

        api_client.get(reverse("v1:github-callback"), {"code": "c", "state": generate_state()})

        # Same GitHub id means same person, so the row is reused under the new login.
        assert User.objects.count() == 1
        assert User.objects.get().username == "carol"


class TestCallbackFailures:
    """Every failure redirects to the app with an error code the UI can show."""

    def test_bad_state_redirects_with_invalid_state(
        self, api_client: APIClient, httpx_mock: HTTPXMock, settings: Any
    ) -> None:

        response = api_client.get(
            reverse("v1:github-callback"), {"code": "auth-code", "state": "forged"}
        )

        assert response.status_code == 302
        assert redirect_params(response)["error"] == ["invalid_state"]
        assert len(httpx_mock.get_requests()) == 0
        assert User.objects.count() == 0

    def test_missing_state_redirects_with_invalid_state(
        self, api_client: APIClient, settings: Any
    ) -> None:

        response = api_client.get(reverse("v1:github-callback"), {"code": "auth-code"})

        assert redirect_params(response)["error"] == ["invalid_state"]

    def test_missing_code_redirects_with_missing_code(
        self, api_client: APIClient, settings: Any
    ) -> None:

        response = api_client.get(reverse("v1:github-callback"), {"state": generate_state()})

        assert redirect_params(response)["error"] == ["missing_code"]

    def test_rejected_exchange_redirects_with_exchange_failed(
        self, api_client: APIClient, httpx_mock: HTTPXMock, settings: Any
    ) -> None:
        httpx_mock.add_response(
            url=TOKEN_URL, method="POST", json={"error": "bad_verification_code"}
        )

        response = api_client.get(
            reverse("v1:github-callback"), {"code": "stale", "state": generate_state()}
        )

        assert redirect_params(response)["error"] == ["exchange_failed"]
        assert User.objects.count() == 0

    def test_failed_profile_fetch_redirects_with_profile_failed(
        self, api_client: APIClient, httpx_mock: HTTPXMock, settings: Any
    ) -> None:
        mock_oauth_exchange(httpx_mock)
        httpx_mock.add_response(
            url=USER_URL, method="GET", json={"message": "Bad credentials"}, status_code=401
        )

        response = api_client.get(
            reverse("v1:github-callback"), {"code": "c", "state": generate_state()}
        )

        assert redirect_params(response)["error"] == ["profile_failed"]
        assert User.objects.count() == 0

    def test_the_error_redirect_carries_no_token(
        self, api_client: APIClient, httpx_mock: HTTPXMock, settings: Any
    ) -> None:
        httpx_mock.add_response(
            url=TOKEN_URL, method="POST", json={"error": "bad_verification_code"}
        )

        response = api_client.get(
            reverse("v1:github-callback"), {"code": "c", "state": generate_state()}
        )

        assert "gho_" not in redirect_url(response)


class TestHandoffExchange:
    def test_code_is_exchanged_for_tokens(self, api_client: APIClient, connected_user: Any) -> None:
        response = api_client.post(
            reverse("v1:handoff-exchange"), {"code": OAuthHandoff.issue(connected_user)}
        )

        assert response.status_code == 200, response.data
        assert response.data["access"]
        assert response.data["refresh"]
        assert response.data["user"]["username"] == "carol"

    def test_code_cannot_be_replayed(self, api_client: APIClient, connected_user: Any) -> None:
        code = OAuthHandoff.issue(connected_user)
        api_client.post(reverse("v1:handoff-exchange"), {"code": code})

        replay = api_client.post(reverse("v1:handoff-exchange"), {"code": code})

        assert replay.status_code == 400
        assert replay.data["error"]["code"] == "invalid_handoff_code"

    def test_unknown_code_is_refused(self, api_client: APIClient) -> None:
        response = api_client.post(reverse("v1:handoff-exchange"), {"code": "never-issued"})

        assert response.status_code == 400

    def test_expired_code_is_refused(self, api_client: APIClient, connected_user: Any) -> None:
        code = OAuthHandoff.issue(connected_user)
        OAuthHandoff.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

        response = api_client.post(reverse("v1:handoff-exchange"), {"code": code})

        assert response.status_code == 400

    def test_missing_code_is_a_400(self, api_client: APIClient) -> None:
        assert api_client.post(reverse("v1:handoff-exchange"), {}).status_code == 400

    def test_exchange_does_not_require_a_session(
        self, api_client: APIClient, connected_user: Any
    ) -> None:
        # The app is mid-launch and holds no Authorization header yet, so a
        # request with no credential at all must still succeed.
        response = api_client.post(
            reverse("v1:handoff-exchange"), {"code": OAuthHandoff.issue(connected_user)}
        )

        assert response.status_code == 200

    def test_returned_tokens_authenticate_the_user(
        self, api_client: APIClient, connected_user: Any
    ) -> None:
        exchanged = api_client.post(
            reverse("v1:handoff-exchange"), {"code": OAuthHandoff.issue(connected_user)}
        ).data

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {exchanged['access']}")
        me = api_client.get(reverse("v1:me"))

        assert me.status_code == 200
        assert me.data["username"] == "carol"


class TestMe:
    def test_returns_the_callers_profile(self, connected_auth_client: APIClient) -> None:
        response = connected_auth_client.get(reverse("v1:me"))

        assert response.status_code == 200
        assert response.data["username"] == "carol"
        assert response.data["is_github_connected"] is True

    def test_never_exposes_the_token(self, connected_auth_client: APIClient) -> None:
        response = connected_auth_client.get(reverse("v1:me"))

        body = str(response.data).lower()
        assert "token" not in body
        assert "gho_" not in body

    def test_requires_authentication(self, api_client: APIClient) -> None:
        assert api_client.get(reverse("v1:me")).status_code == 401


class TestLogout:
    def test_blacklists_the_refresh_token(
        self, connected_auth_client: APIClient, connected_user: Any
    ) -> None:
        token = RefreshToken.for_user(connected_user)

        response = connected_auth_client.post(reverse("v1:logout"), {"refresh": str(token)})

        assert response.status_code == 200
        assert response.data["revoked"] is True
        with pytest.raises(TokenError, match="blacklisted"):
            token.check_blacklist()

    def test_clears_the_stored_github_token(
        self, connected_auth_client: APIClient, connected_user: Any
    ) -> None:
        connected_auth_client.post(reverse("v1:logout"), {})

        connected_user.refresh_from_db()
        assert connected_user.is_github_connected is False

    def test_a_bogus_refresh_token_still_signs_the_user_out(
        self, connected_auth_client: APIClient, connected_user: Any
    ) -> None:
        # The client discards what it holds regardless; reporting failure would
        # leave the app unable to clear its own state.
        response = connected_auth_client.post(reverse("v1:logout"), {"refresh": "not-a-token"})

        assert response.status_code == 200
        assert response.data["revoked"] is False
        connected_user.refresh_from_db()
        assert connected_user.is_github_connected is False

    def test_requires_authentication(self, api_client: APIClient) -> None:
        assert api_client.post(reverse("v1:logout"), {}).status_code == 401


class TestDisconnect:
    def test_clears_the_stored_token(
        self, connected_auth_client: APIClient, connected_user: Any
    ) -> None:
        response = connected_auth_client.delete(reverse("v1:github-disconnect"))

        assert response.status_code == 204
        connected_user.refresh_from_db()
        assert connected_user.is_github_connected is False
        assert connected_user.github_access_token == ""

    def test_keeps_the_account_and_its_tasks(
        self, connected_auth_client: APIClient, connected_user: Any, task: Any
    ) -> None:
        connected_auth_client.delete(reverse("v1:github-disconnect"))

        # Task history must survive so the user does not lose their work.
        assert AgentTask.objects.filter(pk=task.pk).exists()

    def test_requires_authentication(self, api_client: APIClient) -> None:
        assert api_client.delete(reverse("v1:github-disconnect")).status_code == 401

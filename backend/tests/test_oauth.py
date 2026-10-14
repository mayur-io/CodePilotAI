"""Tests for the GitHub OAuth flow: signed state, URL building, code exchange."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from django.conf import settings
from django.core import signing
from pytest_httpx import HTTPXMock

from apps.github.exceptions import GitHubAuthError, GitHubError
from apps.github.oauth import (
    TOKEN_URL,
    build_authorize_url,
    exchange_code_for_token,
    generate_state,
    verify_state,
)

CLIENT_ID = "test-client-id"


@pytest.fixture(autouse=True)
def _github_credentials(settings: Any) -> None:
    settings.GITHUB_CLIENT_ID = CLIENT_ID
    settings.GITHUB_CLIENT_SECRET = "test-client-secret"
    settings.GITHUB_REDIRECT_URI = "http://localhost:8000/api/v1/auth/github/callback"


class TestStateToken:
    def test_generated_state_verifies(self) -> None:
        assert verify_state(generate_state()) is True

    def test_each_state_is_unique(self) -> None:
        assert generate_state() != generate_state()

    def test_tampered_state_is_rejected(self) -> None:
        state = generate_state()
        assert verify_state(state[:-2] + "xy") is False

    def test_arbitrary_string_is_rejected(self) -> None:
        assert verify_state("not-a-signed-state") is False

    def test_state_from_a_different_salt_is_rejected(self) -> None:
        forged = signing.dumps({"nonce": "x"}, salt="some.other.salt")
        assert verify_state(forged) is False

    def test_state_survives_a_short_clock_skew(self) -> None:
        # 5 minutes of skew is well inside the 10 minute window.
        state = generate_state()
        with patch.object(signing.time, "time", return_value=time.time() + 300):
            assert verify_state(state) is True

    def test_state_older_than_the_window_is_rejected(self) -> None:
        # 11 minutes later: past the 600 second max_age.
        state = generate_state()
        with patch.object(signing.time, "time", return_value=time.time() + 660):
            assert verify_state(state) is False


class TestAuthorizeUrl:
    def test_url_targets_github(self) -> None:
        url = build_authorize_url("state-value")
        assert url.startswith("https://github.com/login/oauth/authorize?")

    def test_url_carries_the_configured_client_id(self) -> None:
        query = parse_qs(urlparse(build_authorize_url("s")).query)
        assert query["client_id"] == [CLIENT_ID]

    def test_url_carries_the_redirect_uri(self) -> None:
        query = parse_qs(urlparse(build_authorize_url("s")).query)
        assert query["redirect_uri"] == [settings.GITHUB_REDIRECT_URI]

    def test_url_requests_repo_and_user_scopes(self) -> None:
        query = parse_qs(urlparse(build_authorize_url("s")).query)
        scopes = query["scope"][0].split()
        assert "repo" in scopes
        assert "read:user" in scopes

    def test_url_forwards_the_state(self) -> None:
        query = parse_qs(urlparse(build_authorize_url("my-state")).query)
        assert query["state"] == ["my-state"]


class TestExchangeCodeForToken:
    def test_successful_exchange_returns_the_token(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url=TOKEN_URL,
            method="POST",
            json={
                "access_token": "gho_real_token",
                "refresh_token": "ghr_real_token",
                "scope": "repo,read:user",
                "token_type": "bearer",
            },
        )

        result = exchange_code_for_token("auth-code")

        assert result["access_token"] == "gho_real_token"
        assert result["refresh_token"] == "ghr_real_token"
        assert result["scope"] == "repo,read:user"
        assert result["expires_in"] is None

    def test_client_credentials_are_posted(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=TOKEN_URL, method="POST", json={"access_token": "t"})

        exchange_code_for_token("auth-code")

        body = parse_qs(httpx_mock.get_requests()[0].content.decode())
        assert body["client_id"] == [CLIENT_ID]
        assert body["client_secret"] == ["test-client-secret"]
        assert body["code"] == ["auth-code"]

    def test_redirect_uri_is_included(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=TOKEN_URL, method="POST", json={"access_token": "t"})

        exchange_code_for_token("auth-code")

        body = parse_qs(httpx_mock.get_requests()[0].content.decode())
        assert body["redirect_uri"] == [settings.GITHUB_REDIRECT_URI]

    def test_github_error_response_raises_auth_error(self, httpx_mock: HTTPXMock) -> None:
        # GitHub answers 200 with an `error` key for a bad code, so the status
        # alone is not enough to detect failure.
        httpx_mock.add_response(
            url=TOKEN_URL,
            method="POST",
            json={"error": "bad_verification_code", "error_description": "expired"},
        )

        with pytest.raises(GitHubAuthError, match="bad_verification_code"):
            exchange_code_for_token("stale-code")

    def test_missing_access_token_raises(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=TOKEN_URL, method="POST", json={"scope": "repo"})

        with pytest.raises(GitHubAuthError, match="did not return an access token"):
            exchange_code_for_token("auth-code")

    def test_http_error_status_raises(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url=TOKEN_URL, method="POST", json={"error": "server_error"}, status_code=500
        )

        with pytest.raises(GitHubError, match="token exchange failed"):
            exchange_code_for_token("auth-code")

    def test_non_json_response_raises(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=TOKEN_URL, method="POST", text="<html>nope</html>")

        with pytest.raises(GitHubError, match="non-JSON"):
            exchange_code_for_token("auth-code")

    def test_network_failure_raises(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_exception(httpx.ConnectError("boom"), url=TOKEN_URL, method="POST")

        with pytest.raises(GitHubError, match="Could not reach GitHub"):
            exchange_code_for_token("auth-code")

    def test_expires_in_is_coerced_to_int(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url=TOKEN_URL, method="POST", json={"access_token": "t", "expires_in": "28800"}
        )

        assert exchange_code_for_token("c")["expires_in"] == 28800

    def test_booleans_are_not_mistaken_for_expiry_values(self, httpx_mock: HTTPXMock) -> None:
        # `bool` is a subclass of `int`, so a naive int() coercion would turn a
        # true into 1 second of token life.
        httpx_mock.add_response(
            url=TOKEN_URL, method="POST", json={"access_token": "t", "expires_in": True}
        )

        assert exchange_code_for_token("c")["expires_in"] is None

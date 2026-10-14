"""Tests for the GitHub REST client: headers, retries, error mapping, scrubbing.

Uses ``pytest-httpx``, which raises on any unregistered request. That guarantee
matters here: the client talks to a live third-party API, so a silent escape to
the network would make these tests pass or fail for reasons unrelated to the code
under test.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from apps.github.client import MAX_RETRIES, GitHubClient
from apps.github.exceptions import (
    GitHubAuthError,
    GitHubConflictError,
    GitHubError,
    GitHubNotFoundError,
    GitHubRateLimitError,
)

TOKEN = "gho_a_token_that_must_never_be_logged"
API = "https://api.github.com"

# Retry backoff sleeps for real seconds unless neutralised. Every test in this
# module either retries or must not, so it is neutralised for the whole module.
pytestmark = pytest.mark.usefixtures("no_sleep")


def build_client() -> GitHubClient:
    return GitHubClient(TOKEN)


class TestConstruction:
    def test_missing_token_is_rejected(self) -> None:
        with pytest.raises(GitHubAuthError, match="access token is required"):
            GitHubClient("")

    def test_context_manager_closes_the_client(self) -> None:
        with build_client() as client:
            assert client is not None

    def test_borrowed_client_is_not_closed(self) -> None:
        transport = httpx.Client(base_url=API)
        client = GitHubClient(TOKEN, client=transport)

        client.close()

        assert not transport.is_closed
        transport.close()


class TestRequestHeaders:
    def test_bearer_token_and_api_version_are_sent(
        self, httpx_mock: HTTPXMock, any_query: Any
    ) -> None:
        httpx_mock.add_response(url=any_query(f"{API}/user"), json={"login": "carol"})

        build_client().get_authenticated_user()

        request = httpx_mock.get_requests()[0]
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
        assert request.headers["Accept"] == "application/vnd.github+json"
        assert "CodePilotAI" in request.headers["User-Agent"]

    def test_204_returns_an_empty_dict(self, httpx_mock: HTTPXMock, any_query: Any) -> None:
        httpx_mock.add_response(url=any_query(f"{API}/repos/a/b"), method="DELETE", status_code=204)

        assert build_client().request("DELETE", "/repos/a/b") == {}


class TestErrorMapping:
    def test_401_becomes_auth_error(self, httpx_mock: HTTPXMock, any_query: Any) -> None:
        httpx_mock.add_response(
            url=any_query(f"{API}/user"), json={"message": "Bad credentials"}, status_code=401
        )

        with pytest.raises(GitHubAuthError, match="reconnect"):
            build_client().get_authenticated_user()

    def test_401_is_not_retried(
        self, httpx_mock: HTTPXMock, any_query: Any, request_count: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(f"{API}/user"),
            json={"message": "Bad credentials"},
            status_code=401,
            is_reusable=True,
        )

        with pytest.raises(GitHubAuthError):
            build_client().get_authenticated_user()

        assert request_count() == 1

    def test_404_becomes_not_found(self, httpx_mock: HTTPXMock, any_query: Any) -> None:
        httpx_mock.add_response(url=any_query(f"{API}/repos/a/missing"), status_code=404)

        with pytest.raises(GitHubNotFoundError):
            build_client().get_repository("a/missing")

    def test_409_becomes_conflict(self, httpx_mock: HTTPXMock, any_query: Any) -> None:
        httpx_mock.add_response(url=any_query(f"{API}/user/repos"), method="POST", status_code=409)

        with pytest.raises(GitHubConflictError):
            build_client().create_repository(name="dupe")

    def test_429_becomes_rate_limit_error(self, httpx_mock: HTTPXMock, any_query: Any) -> None:
        httpx_mock.add_response(
            url=any_query(f"{API}/user"),
            json={"message": "rate limited"},
            status_code=429,
            headers={"Retry-After": "1"},
            is_reusable=True,
        )

        with pytest.raises(GitHubRateLimitError) as exc:
            build_client().get_authenticated_user()

        assert exc.value.retry_after == 1

    def test_403_with_no_remaining_quota_becomes_rate_limit(
        self, httpx_mock: HTTPXMock, any_query: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(f"{API}/user"),
            json={"message": "limit exceeded"},
            status_code=403,
            headers={"X-RateLimit-Remaining": "0"},
            is_reusable=True,
        )

        with pytest.raises(GitHubRateLimitError):
            build_client().get_authenticated_user()

    def test_403_with_quota_left_is_a_plain_error(
        self, httpx_mock: HTTPXMock, any_query: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(f"{API}/user"),
            json={"message": "forbidden"},
            status_code=403,
            headers={"X-RateLimit-Remaining": "4999"},
        )

        with pytest.raises(GitHubError, match="denied"):
            build_client().get_authenticated_user()

    def test_500_becomes_server_error(self, httpx_mock: HTTPXMock, any_query: Any) -> None:
        httpx_mock.add_response(
            url=any_query(f"{API}/user"),
            json={"message": "oops"},
            status_code=500,
            is_reusable=True,
        )

        with pytest.raises(GitHubError, match="server error"):
            build_client().get_authenticated_user()

    def test_carries_the_status_code_on_the_exception(
        self, httpx_mock: HTTPXMock, any_query: Any
    ) -> None:
        httpx_mock.add_response(url=any_query(f"{API}/user"), status_code=404)

        with pytest.raises(GitHubNotFoundError) as exc:
            build_client().get_authenticated_user()

        assert exc.value.status_code == 404

    def test_carries_the_remaining_quota_on_the_exception(
        self, httpx_mock: HTTPXMock, any_query: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(f"{API}/user"), status_code=403, headers={"X-RateLimit-Remaining": "17"}
        )

        with pytest.raises(GitHubError) as exc:
            build_client().get_authenticated_user()

        assert exc.value.rate_limit_remaining == 17


class TestTokenScrubbing:
    def test_token_in_an_error_body_is_redacted(
        self, httpx_mock: HTTPXMock, any_query: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(f"{API}/user"),
            json={"message": f"bad token {TOKEN}"},
            status_code=404,
        )

        with pytest.raises(GitHubNotFoundError) as exc:
            build_client().get_authenticated_user()

        message = str(exc.value)
        assert TOKEN not in message
        assert "***REDACTED***" in message

    def test_token_in_a_500_body_is_redacted(self, httpx_mock: HTTPXMock, any_query: Any) -> None:
        httpx_mock.add_response(
            url=any_query(f"{API}/user"),
            json={"message": f"echo {TOKEN}"},
            status_code=500,
            is_reusable=True,
        )

        with pytest.raises(GitHubError) as exc:
            build_client().get_authenticated_user()

        assert TOKEN not in str(exc.value)


class TestRetries:
    def test_transient_500_is_retried_then_succeeds(
        self, httpx_mock: HTTPXMock, any_query: Any, request_count: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(f"{API}/user"),
            json={"message": "oops"},
            status_code=500,
            is_reusable=True,
        )
        httpx_mock.add_response(url=any_query(f"{API}/user"), json={"login": "carol"})

        result = build_client().get_authenticated_user()

        assert result == {"login": "carol"}
        assert request_count() == 2

    def test_rate_limit_is_retried(self, httpx_mock: HTTPXMock, any_query: Any) -> None:
        httpx_mock.add_response(
            url=any_query(f"{API}/user"),
            json={"message": "slow down"},
            status_code=429,
            headers={"Retry-After": "0"},
            is_reusable=True,
        )
        httpx_mock.add_response(url=any_query(f"{API}/user"), json={"login": "carol"})

        assert build_client().get_authenticated_user() == {"login": "carol"}

    def test_transport_error_is_retried_then_succeeds(
        self, httpx_mock: HTTPXMock, any_query: Any, request_count: Any
    ) -> None:
        httpx_mock.add_exception(httpx.ConnectError("refused"), is_reusable=True)
        httpx_mock.add_response(url=any_query(f"{API}/user"), json={"login": "carol"})

        assert build_client().get_authenticated_user() == {"login": "carol"}
        assert request_count() == 2

    def test_persistent_500_gives_up_after_the_retry_budget(
        self, httpx_mock: HTTPXMock, any_query: Any, request_count: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(f"{API}/user"),
            json={"message": "oops"},
            status_code=500,
            is_reusable=True,
        )

        with pytest.raises(GitHubError):
            build_client().get_authenticated_user()

        # One initial attempt plus MAX_RETRIES retries.
        assert request_count() == MAX_RETRIES + 1

    def test_persistent_timeout_reports_a_timeout(
        self, httpx_mock: HTTPXMock, request_count: Any
    ) -> None:
        httpx_mock.add_exception(httpx.ReadTimeout("too slow"), is_reusable=True)

        with pytest.raises(GitHubError, match="timed out"):
            build_client().get_authenticated_user()

        assert request_count() == MAX_RETRIES + 1

    def test_backoff_delays_grow_but_stay_capped(self) -> None:
        client = build_client()
        delays = [client._backoff_delay(attempt) for attempt in range(8)]

        assert delays == sorted(delays), "backoff must not shrink between attempts"
        assert delays[0] < delays[1], "backoff must actually back off"
        assert max(delays) <= GitHubClient.MAX_BACKOFF_SECONDS, "backoff must stay bounded"


class TestPagination:
    def test_pages_are_followed_until_a_short_page(
        self, httpx_mock: HTTPXMock, any_query: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(f"{API}/user/repos"), json=[{"id": i} for i in range(100)]
        )
        httpx_mock.add_response(url=any_query(f"{API}/user/repos"), json=[{"id": 100}])

        items = list(build_client().paginate("/user/repos", per_page=100))

        assert len(items) == 101

    def test_page_number_is_sent_to_github(self, httpx_mock: HTTPXMock, any_query: Any) -> None:
        httpx_mock.add_response(url=any_query(f"{API}/user/repos"), json=[{"id": 1}])

        list(build_client().paginate("/user/repos", per_page=100))

        assert httpx_mock.get_requests()[0].url.params["page"] == "1"

    def test_per_page_is_sent_to_github(self, httpx_mock: HTTPXMock, any_query: Any) -> None:
        httpx_mock.add_response(url=any_query(f"{API}/user/repos"), json=[{"id": 1}])

        list(build_client().paginate("/user/repos", per_page=25))

        assert httpx_mock.get_requests()[0].url.params["per_page"] == "25"

    def test_empty_first_page_ends_immediately(self, httpx_mock: HTTPXMock, any_query: Any) -> None:
        httpx_mock.add_response(url=any_query(f"{API}/user/repos"), json=[])

        assert list(build_client().paginate("/user/repos")) == []

    def test_pagination_stops_at_the_page_cap(self, httpx_mock: HTTPXMock, any_query: Any) -> None:
        # A full page every time would otherwise loop forever.
        httpx_mock.add_response(
            url=any_query(f"{API}/user/repos"),
            json=[{"id": i} for i in range(100)],
            is_reusable=True,
        )

        items = list(build_client().paginate("/user/repos", per_page=100, max_pages=3))

        assert len(items) == 300


class TestPullRequests:
    def test_create_pull_request_sends_the_expected_body(
        self, httpx_mock: HTTPXMock, any_query: Any, sent_json: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(f"{API}/repos/carol/demo-app/pulls"),
            method="POST",
            json={"number": 7, "html_url": "https://github.com/carol/demo-app/pull/7"},
            status_code=201,
        )

        result = build_client().create_pull_request(
            "carol/demo-app",
            head="codepilot/add-login",
            base="main",
            title="feat: add login form",
            body="## What changed\n- Added login form",
        )

        assert result["number"] == 7
        sent = sent_json()
        assert sent["head"] == "codepilot/add-login"
        assert sent["base"] == "main"
        assert sent["draft"] is False
        assert sent["maintainer_can_modify"] is True

    def test_merge_uses_squash_by_default(
        self, httpx_mock: HTTPXMock, any_query: Any, sent_json: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(f"{API}/repos/carol/demo-app/pulls/7"),
            method="PUT",
            json={"merged": True},
        )

        result = build_client().merge_pull_request("carol/demo-app", 7)

        assert result == {"merged": True}
        assert sent_json() == {"merge_method": "squash"}

    def test_merge_passes_a_custom_method(
        self, httpx_mock: HTTPXMock, any_query: Any, sent_json: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(f"{API}/repos/carol/demo-app/pulls/7"),
            method="PUT",
            json={"merged": True},
        )

        build_client().merge_pull_request("carol/demo-app", 7, merge_method="rebase")

        assert sent_json()["merge_method"] == "rebase"

    def test_merge_omits_commit_title_when_not_given(
        self, httpx_mock: HTTPXMock, any_query: Any, sent_json: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(f"{API}/repos/carol/demo-app/pulls/7"),
            method="PUT",
            json={"merged": True},
        )

        build_client().merge_pull_request("carol/demo-app", 7)

        assert "commit_title" not in sent_json()


class TestBranches:
    def test_get_branch_returns_the_head_sha(self, httpx_mock: HTTPXMock, any_query: Any) -> None:
        httpx_mock.add_response(
            url=any_query(f"{API}/repos/carol/demo-app/branches/main"),
            json={"name": "main", "commit": {"sha": "deadbeef"}},
        )

        result = build_client().get_branch("carol/demo-app", "main")

        assert result["commit"]["sha"] == "deadbeef"

    def test_create_repository_posts_the_name_and_visibility(
        self, httpx_mock: HTTPXMock, any_query: Any, sent_json: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(f"{API}/user/repos"), method="POST", json={"id": 1}, status_code=201
        )

        build_client().create_repository(name="new-app", private=False)

        assert sent_json() == {
            "name": "new-app",
            "description": "",
            "private": False,
            "auto_init": True,
        }


class TestResponseDecoding:
    def test_non_json_success_body_raises(self, httpx_mock: HTTPXMock, any_query: Any) -> None:
        # httpx raises json.JSONDecodeError, a ValueError subclass, when the
        # body cannot be decoded. A 200 with an HTML body means a proxy or a
        # captive portal answered instead of GitHub.
        httpx_mock.add_response(url=any_query(f"{API}/user"), text="<html>maintenance</html>")

        with pytest.raises(json.JSONDecodeError):
            build_client().get_authenticated_user()

    def test_json_array_payloads_are_returned_verbatim(
        self, httpx_mock: HTTPXMock, any_query: Any
    ) -> None:
        payload: list[dict[str, Any]] = [{"name": "main"}]
        httpx_mock.add_response(url=any_query(f"{API}/repos/a/b/branches"), json=payload)

        assert build_client().request("GET", "/repos/a/b/branches") == payload

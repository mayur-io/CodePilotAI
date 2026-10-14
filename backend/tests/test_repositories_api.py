"""Tests for the repositories API and its GitHub-backed services."""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone
from pytest_httpx import HTTPXMock
from rest_framework.test import APIClient

from apps.github.exceptions import GitHubAuthError, GitHubNotFoundError
from apps.repositories.models import Branch, Repository
from apps.repositories.serializers import RepositoryCreateSerializer
from apps.repositories.services import (
    GitHubNotConnectedError,
    create_repository,
    github_client_for,
    list_branches,
    sync_repositories_from_github,
    upsert_repository,
)

pytestmark = pytest.mark.django_db

# Mirrors conftest.GITHUB_TOKEN. Duplicated rather than imported so the module
# stays importable under pytest's importlib import mode.
GITHUB_TOKEN = "gho_testtoken_do_not_log_me"

API = "https://api.github.com"
REPOS_URL = f"{API}/user/repos"
BRANCHES_URL = f"{API}/repos/carol/demo-app/branches"


class TestRepositoryIsolation:
    def test_list_only_returns_the_callers_repositories(
        self,
        connected_auth_client: APIClient,
        repository: Repository,
        other_repository: Repository,
    ) -> None:
        response = connected_auth_client.get(reverse("v1:repository-list"))

        assert response.status_code == 200
        assert [row["id"] for row in response.data["results"]] == [repository.pk]

    def test_detail_of_another_users_repository_is_404(
        self, connected_auth_client: APIClient, other_repository: Repository
    ) -> None:
        response = connected_auth_client.get(
            reverse("v1:repository-detail", args=[other_repository.pk])
        )

        assert response.status_code == 404

    def test_list_requires_authentication(
        self, api_client: APIClient, repository: Repository
    ) -> None:
        assert api_client.get(reverse("v1:repository-list")).status_code == 401

    def test_search_filters_by_full_name(
        self,
        connected_auth_client: APIClient,
        repository: Repository,
        other_repository: Repository,
    ) -> None:
        response = connected_auth_client.get(reverse("v1:repository-list"), {"search": "demo-app"})

        assert [row["id"] for row in response.data["results"]] == [repository.pk]

    def test_branches_endpoint_is_scoped_to_the_owner(
        self, connected_auth_client: APIClient, other_repository: Repository
    ) -> None:
        response = connected_auth_client.get(
            reverse("v1:repository-branches", args=[other_repository.pk])
        )

        assert response.status_code == 404


class TestRepositorySerializer:
    def test_clone_url_is_derived_from_full_name(self, repository: Repository) -> None:
        assert repository.clone_url == "https://github.com/carol/demo-app.git"

    def test_needs_sync_is_true_before_any_fetch(self, repository: Repository) -> None:
        assert repository.needs_sync is True

    def test_needs_sync_is_false_right_after_a_fetch(self, repository: Repository) -> None:
        repository.mirror_synced_at = timezone.now()

        assert repository.needs_sync is False


class TestCreateSerializerValidation:
    def test_valid_name_is_trimmed(self) -> None:
        serializer = RepositoryCreateSerializer(data={"name": "  my-app  "})

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["name"] == "my-app"

    @pytest.mark.parametrize("bad", ["my app", "app/name", "app!", "app#"])
    def test_unsupported_characters_are_rejected_before_any_api_call(self, bad: str) -> None:
        serializer = RepositoryCreateSerializer(data={"name": bad})

        assert not serializer.is_valid()
        assert "unsupported characters" in str(serializer.errors)

    def test_private_defaults_to_true(self) -> None:
        serializer = RepositoryCreateSerializer(data={"name": "my-app"})

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["private"] is True

    def test_auto_init_defaults_to_true(self) -> None:
        serializer = RepositoryCreateSerializer(data={"name": "my-app"})

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["auto_init"] is True


class TestGitHubClientFactory:
    def test_disconnected_user_cannot_build_a_client(self, user: Any) -> None:
        with pytest.raises(GitHubNotConnectedError, match="Connect your GitHub account"):
            github_client_for(user)

    def test_connected_user_gets_a_client(self, connected_user: Any) -> None:
        client = github_client_for(connected_user)

        client.close()


class TestUpsertRepository:
    def test_new_repo_is_created(self, connected_user: Any) -> None:
        repo = upsert_repository(
            connected_user,
            {
                "id": 99,
                "name": "new-app",
                "full_name": "carol/new-app",
                "default_branch": "main",
                "private": True,
                "language": "Go",
            },
        )

        assert repo.pk is not None
        assert repo.full_name == "carol/new-app"
        assert repo.primary_language == "Go"

    def test_existing_repo_is_updated_in_place(
        self, connected_user: Any, repository: Repository
    ) -> None:
        upsert_repository(
            connected_user,
            {
                "id": repository.github_id,
                "name": "renamed",
                "full_name": "carol/renamed",
                "default_branch": "trunk",
                "private": False,
            },
        )

        assert Repository.objects.filter(user=connected_user).count() == 1
        repository.refresh_from_db()
        assert repository.full_name == "carol/renamed"
        assert repository.default_branch == "trunk"
        assert repository.is_private is False

    def test_missing_default_branch_falls_back_to_main(self, connected_user: Any) -> None:
        repo = upsert_repository(connected_user, {"id": 5, "name": "b", "full_name": "carol/b"})

        assert repo.default_branch == "main"


class TestSyncFromGitHub:
    def test_repos_are_imported(
        self, connected_user: Any, httpx_mock: HTTPXMock, any_query: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(REPOS_URL),
            method="GET",
            json=[{"id": 1, "name": "a", "full_name": "carol/a", "default_branch": "main"}],
        )

        count = sync_repositories_from_github(connected_user)

        assert count == 1
        assert Repository.objects.filter(user=connected_user, full_name="carol/a").exists()

    def test_multiple_pages_are_followed(
        self, connected_user: Any, httpx_mock: HTTPXMock, any_query: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(REPOS_URL),
            method="GET",
            json=[{"id": i, "name": f"r{i}", "full_name": f"carol/r{i}"} for i in range(100)],
        )
        httpx_mock.add_response(
            url=any_query(REPOS_URL),
            method="GET",
            json=[{"id": 999, "name": "last", "full_name": "carol/last"}],
        )

        sync_repositories_from_github(connected_user)

        assert Repository.objects.filter(user=connected_user).count() == 101

    def test_auth_failure_surfaces_as_auth_error(
        self, connected_user: Any, httpx_mock: HTTPXMock, any_query: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(REPOS_URL),
            method="GET",
            json={"message": "Bad credentials"},
            status_code=401,
        )

        with pytest.raises(GitHubAuthError):
            sync_repositories_from_github(connected_user)

    def test_sync_does_not_touch_another_users_rows(
        self,
        connected_user: Any,
        other_user: Any,
        httpx_mock: HTTPXMock,
        any_query: Any,
    ) -> None:
        other = Repository.objects.create(
            user=other_user, github_id=7777, name="secret", full_name="bob/secret"
        )
        httpx_mock.add_response(
            url=any_query(REPOS_URL),
            method="GET",
            json=[{"id": 7777, "name": "stolen", "full_name": "carol/stolen"}],
        )

        sync_repositories_from_github(connected_user)

        other.refresh_from_db()
        assert other.full_name == "bob/secret"
        assert Repository.objects.filter(user=other_user).count() == 1


class TestCreateRepository:
    def test_repo_is_created_on_github_and_recorded_locally(
        self, connected_user: Any, httpx_mock: HTTPXMock, any_query: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(REPOS_URL),
            method="POST",
            json={
                "id": 31337,
                "name": "brand-new",
                "full_name": "carol/brand-new",
                "default_branch": "main",
                "private": True,
            },
            status_code=201,
        )

        repo = create_repository(connected_user, {"name": "brand-new", "private": True})

        assert repo.full_name == "carol/brand-new"
        assert Repository.objects.filter(github_id=31337).exists()

    def test_github_conflict_surfaces(
        self, connected_user: Any, httpx_mock: HTTPXMock, any_query: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(REPOS_URL),
            method="POST",
            json={"message": "Repository creation failed.", "errors": [{"code": "custom"}]},
            status_code=422,
        )

        with pytest.raises(Exception, match="GitHub request failed"):
            create_repository(connected_user, {"name": "dupe"})


class TestListBranches:
    def test_remote_branches_are_mirrored_locally(
        self,
        connected_user: Any,
        repository: Repository,
        httpx_mock: HTTPXMock,
        any_query: Any,
    ) -> None:
        httpx_mock.add_response(
            url=any_query(BRANCHES_URL),
            method="GET",
            json=[
                {"name": "main", "commit": {"sha": "aaa"}},
                {"name": "codepilot/fix-typo", "commit": {"sha": "bbb"}},
            ],
        )

        result = list_branches(connected_user, repository)

        assert len(result) == 2
        main = Branch.objects.get(repository=repository, name="main")
        assert main.kind == Branch.Kind.DEFAULT
        assert main.head_sha == "aaa"
        feature = Branch.objects.get(repository=repository, name="codepilot/fix-typo")
        assert feature.kind == Branch.Kind.FEATURE
        assert feature.head_sha == "bbb"

    def test_missing_repository_surfaces_not_found(
        self,
        connected_user: Any,
        repository: Repository,
        httpx_mock: HTTPXMock,
        any_query: Any,
    ) -> None:
        httpx_mock.add_response(
            url=any_query(BRANCHES_URL),
            method="GET",
            json={"message": "Not Found"},
            status_code=404,
        )

        with pytest.raises(GitHubNotFoundError):
            list_branches(connected_user, repository)


class TestCreateEndpoint:
    def test_post_creates_and_returns_the_repository(
        self, connected_auth_client: APIClient, httpx_mock: HTTPXMock, any_query: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(REPOS_URL),
            method="POST",
            json={
                "id": 8080,
                "name": "from-api",
                "full_name": "carol/from-api",
                "default_branch": "main",
            },
            status_code=201,
        )

        response = connected_auth_client.post(
            reverse("v1:repository-list"),
            {"name": "from-api", "private": True},
            format="json",
        )

        assert response.status_code == 201, response.data
        assert response.data["full_name"] == "carol/from-api"

    def test_invalid_name_fails_without_calling_github(
        self, connected_auth_client: APIClient, httpx_mock: HTTPXMock, request_count: Any
    ) -> None:
        response = connected_auth_client.post(
            reverse("v1:repository-list"),
            {"name": "bad name!"},
            format="json",
        )

        assert response.status_code == 400
        assert request_count() == 0

    def test_create_requires_github_connection(self, auth_client: APIClient) -> None:
        response = auth_client.post(
            reverse("v1:repository-list"), {"name": "needs-github"}, format="json"
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "github_not_connected"


class TestSyncEndpoint:
    def test_sync_imports_repositories(
        self, connected_auth_client: APIClient, httpx_mock: HTTPXMock, any_query: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(REPOS_URL),
            method="GET",
            json=[{"id": 1, "name": "a", "full_name": "carol/a"}],
        )

        response = connected_auth_client.post(reverse("v1:repository-sync"))

        assert response.status_code == 200
        assert response.data["count"] == 1
        assert response.data["synced"] is True

    def test_rate_limit_is_reported_as_429(
        self, connected_auth_client: APIClient, httpx_mock: HTTPXMock, any_query: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(REPOS_URL),
            method="GET",
            json={"message": "API rate limit exceeded"},
            status_code=403,
            headers={"X-RateLimit-Remaining": "0", "Retry-After": "0"},
            is_reusable=True,
        )

        response = connected_auth_client.post(reverse("v1:repository-sync"))

        assert response.status_code == 429
        assert response.data["error"]["code"] == "github_rate_limited"

    def test_sync_requires_github_connection(self, auth_client: APIClient) -> None:
        response = auth_client.post(reverse("v1:repository-sync"))

        assert response.status_code == 409


class TestBranchesEndpoint:
    def test_branches_are_fetched_and_returned(
        self,
        connected_auth_client: APIClient,
        repository: Repository,
        httpx_mock: HTTPXMock,
        any_query: Any,
    ) -> None:
        httpx_mock.add_response(
            url=any_query(BRANCHES_URL),
            method="GET",
            json=[{"name": "main", "commit": {"sha": "aaa"}}],
        )

        response = connected_auth_client.get(
            reverse("v1:repository-branches", args=[repository.pk])
        )

        assert response.status_code == 200
        assert [row["name"] for row in response.data] == ["main"]

    def test_disconnected_user_gets_a_clear_error(
        self, auth_client: APIClient, repository: Repository
    ) -> None:
        # A disconnected user cannot call GitHub at all.
        repository.user.clear_github_token()
        repository.user.save()

        response = auth_client.get(reverse("v1:repository-branches", args=[repository.pk]))

        assert response.status_code == 404


class TestTokenIsNeverLeaked:
    def test_error_response_does_not_contain_the_token(
        self, connected_auth_client: APIClient, httpx_mock: HTTPXMock, any_query: Any
    ) -> None:
        httpx_mock.add_response(
            url=any_query(REPOS_URL),
            method="GET",
            json={"message": f"Bad credentials for {GITHUB_TOKEN}"},
            status_code=401,
        )

        response = connected_auth_client.post(reverse("v1:repository-sync"))

        assert response.status_code == 401
        assert GITHUB_TOKEN not in json.dumps(response.data, default=str)

    def test_serialized_repository_never_exposes_token_fields(
        self, connected_auth_client: APIClient, repository: Repository
    ) -> None:
        response = connected_auth_client.get(reverse("v1:repository-detail", args=[repository.pk]))

        assert response.status_code == 200
        body = json.dumps(response.data, default=str).lower()
        assert "token" not in body
        assert "mirror_path" not in body

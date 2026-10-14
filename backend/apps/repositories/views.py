"""API views for repositories and branches.

Every queryset is scoped to ``request.user`` so one account can never read or
mutate another's repositories.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.github.exceptions import (
    GitHubAuthError,
    GitHubConflictError,
    GitHubError,
    GitHubNotFoundError,
    GitHubRateLimitError,
)
from apps.repositories import services
from apps.repositories.models import Repository
from apps.repositories.serializers import (
    BranchSerializer,
    RepositoryCreateSerializer,
    RepositorySerializer,
    RepositorySyncSerializer,
)
from config.observability import get_logger

logger = get_logger(__name__)


def _github_error_response(exc: Exception) -> Response:
    """Map a typed GitHub exception onto the standard error envelope."""
    if isinstance(exc, services.GitHubNotConnectedError):
        code, http_status = "github_not_connected", status.HTTP_409_CONFLICT
    elif isinstance(exc, GitHubAuthError):
        code, http_status = "github_unauthorized", status.HTTP_401_UNAUTHORIZED
    elif isinstance(exc, GitHubNotFoundError):
        code, http_status = "github_not_found", status.HTTP_404_NOT_FOUND
    elif isinstance(exc, GitHubConflictError):
        code, http_status = "github_conflict", status.HTTP_409_CONFLICT
    elif isinstance(exc, GitHubRateLimitError):
        code, http_status = "github_rate_limited", status.HTTP_429_TOO_MANY_REQUESTS
    else:
        code, http_status = "github_error", status.HTTP_502_BAD_GATEWAY
    return Response({"error": {"code": code, "message": str(exc)}}, status=http_status)


class RepositoryViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """List, search, and inspect the repositories a user has connected."""

    serializer_class = RepositorySerializer
    permission_classes = (IsAuthenticated,)
    throttle_scope = "github"
    filterset_fields = ("is_private", "is_fork", "is_archived", "primary_language")
    search_fields = ("full_name", "name", "description")
    ordering_fields = ("last_opened_at", "created_at", "name", "full_name")
    ordering = ("-last_opened_at",)

    def get_queryset(self) -> Any:
        """Return only the requesting user's repositories."""
        return Repository.objects.filter(user=self.request.user).select_related("user")

    @extend_schema(
        summary="List repositories",
        description="Repositories already connected. Use `sync` to pull the full list from GitHub.",
        responses=RepositorySerializer(many=True),
    )
    def list(self, request: Any, *args: Any, **kwargs: Any) -> Response:
        """Return a page of connected repositories."""
        return super().list(request, *args, **kwargs)

    @extend_schema(
        summary="Create a repository",
        request=RepositoryCreateSerializer,
        responses={201: RepositorySerializer},
    )
    def create(self, request: Any, *args: Any, **kwargs: Any) -> Response:
        """Create a repository on GitHub and connect it to this account."""
        serializer = RepositoryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            repository = services.create_repository(request.user, serializer.validated_data)
        except services.GitHubNotConnectedError as exc:
            return _github_error_response(exc)
        except GitHubError as exc:
            logger.warning("repository_create_failed", user_id=request.user.pk, reason=str(exc))
            return _github_error_response(exc)

        Repository.objects.filter(pk=repository.pk).update(task_count=0)
        return Response(
            RepositorySerializer(repository).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(summary="Repository detail", responses=RepositorySerializer)
    def retrieve(self, request: Any, *args: Any, **kwargs: Any) -> Response:
        """Return one connected repository."""
        return super().retrieve(request, *args, **kwargs)

    @action(detail=False, methods=["post"], url_path="sync")
    @extend_schema(
        summary="Sync repositories from GitHub",
        request=None,
        responses={200: RepositorySyncSerializer},
    )
    def sync(self, request: Any) -> Response:
        """Import the user's GitHub repositories into their local list."""
        try:
            count = services.sync_repositories_from_github(request.user)
        except services.GitHubNotConnectedError as exc:
            return _github_error_response(exc)
        except GitHubError as exc:
            logger.warning("repository_sync_failed", user_id=request.user.pk, reason=str(exc))
            return _github_error_response(exc)

        return Response(
            {"detail": f"Synced {count} repositories.", "count": count, "synced": True},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"], url_path="branches")
    @extend_schema(summary="List branches", responses=BranchSerializer(many=True))
    def branches(self, request: Any, pk: int | None = None) -> Response:
        """Fetch the repository's branches from GitHub and mirror them locally."""
        repository = self.get_object()
        try:
            services.list_branches(request.user, repository)
        except services.GitHubNotConnectedError as exc:
            return _github_error_response(exc)
        except GitHubError as exc:
            logger.warning("branch_list_failed", repository_id=repository.pk, reason=str(exc))
            return _github_error_response(exc)

        branches = repository.branches.order_by("name")
        return Response(BranchSerializer(branches, many=True).data)


__all__: list[str] = ["RepositoryViewSet"]

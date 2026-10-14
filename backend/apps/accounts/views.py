"""Authentication views: GitHub OAuth sign-in, token exchange, profile, logout."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import OAuthHandoff, User
from apps.accounts.serializers import (
    AuthorizeURLSerializer,
    HandoffExchangeSerializer,
    LogoutSerializer,
    TokenPairSerializer,
    UserSerializer,
)
from apps.github.client import GitHubClient
from apps.github.exceptions import GitHubAuthError, GitHubError
from apps.github.oauth import (
    build_authorize_url,
    exchange_code_for_token,
    generate_state,
    verify_state,
)
from config.observability import get_logger

logger = get_logger(__name__)


def issue_token_pair(user: User) -> dict[str, str]:
    """Mint a fresh access/refresh pair for ``user``."""
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def build_app_callback_url(code: str) -> str:
    """Build the deep link that returns the app to the foreground with a code."""
    return _app_callback(code=code)


def build_app_callback_url_from_error(code: str, message: str) -> str:
    """Build the deep link that returns the app with a failure to show."""
    return _app_callback(error=code, error_description=message)


def _app_callback(**params: str) -> str:
    """Assemble the callback deep link.

    ``urlencode`` already escapes the values, so the whole URL must not be
    escaped again -- doing so turns ``?`` and ``&`` into ``%3F``/``%26`` and the
    app receives a link with no query string at all.
    """
    scheme = settings.ANDROID_DEEP_LINK_SCHEME
    return f"{scheme}://auth/callback?{urlencode(params)}"


class GitHubAuthorizeStartView(APIView):
    """Step 1 of sign-in: hand the app a GitHub URL and a signed state token."""

    permission_classes = (AllowAny,)
    authentication_classes = ()
    throttle_scope = "github"

    @extend_schema(
        summary="Begin GitHub sign-in",
        description=(
            "Returns the GitHub authorization URL to open in a Custom Tab. The "
            "returned state is signed and expires in 10 minutes."
        ),
        responses=AuthorizeURLSerializer,
    )
    def get(self, request: Any) -> Response:
        if not settings.GITHUB_CLIENT_ID:
            return Response(
                {
                    "error": {
                        "code": "github_not_configured",
                        "message": "GitHub OAuth is not configured on this server.",
                    }
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {
                "authorize_url": build_authorize_url(generate_state()),
                "expires_in": 600,
            }
        )


class GitHubCallbackView(APIView):
    """Step 2: exchange GitHub's code, upsert the user, redirect with a handoff code.

    This endpoint is reached by the browser, not the app, so it answers with a
    redirect. No JWT ever appears in the URL.
    """

    permission_classes = (AllowAny,)
    authentication_classes = ()
    throttle_scope = "github"

    @extend_schema(
        summary="GitHub OAuth callback",
        description=(
            "Exchanges the authorization code and redirects to the app with a one-time code."
        ),
        parameters=[
            OpenApiParameter("code", str, location=OpenApiParameter.QUERY, required=True),
            OpenApiParameter("state", str, location=OpenApiParameter.QUERY, required=False),
        ],
        responses={302: None},
    )
    def get(self, request: Any) -> Response:
        code = request.query_params.get("code", "")
        state = request.query_params.get("state", "")

        if not state or not verify_state(state):
            return self._redirect_with_error("invalid_state", "The sign-in request expired.")
        if not code:
            return self._redirect_with_error(
                "missing_code",
                "GitHub did not return an authorization code.",
            )

        try:
            token_payload = exchange_code_for_token(code)
        except (GitHubAuthError, GitHubError) as exc:
            logger.warning("github_oauth_exchange_failed", reason=str(exc))
            return self._redirect_with_error(
                "exchange_failed",
                "Could not complete GitHub sign-in.",
            )

        access_token = str(token_payload["access_token"])
        try:
            profile = self._fetch_profile(access_token)
        except GitHubError as exc:
            logger.warning("github_profile_fetch_failed", reason=str(exc))
            return self._redirect_with_error(
                "profile_failed",
                "Could not read your GitHub profile.",
            )

        try:
            user = self._upsert_user(profile, token_payload)
        except Exception:
            logger.exception("github_user_upsert_failed")
            return self._redirect_with_error("account_failed", "Could not save your account.")

        handoff_code = OAuthHandoff.issue(user)
        logger.info("github_signin_success", user_id=user.pk, username=user.username)
        return Response(
            status=status.HTTP_302_FOUND,
            headers={"Location": build_app_callback_url(handoff_code)},
        )

    @staticmethod
    def _fetch_profile(access_token: str) -> dict[str, Any]:
        with GitHubClient(access_token) as client:
            return client.get_authenticated_user()

    @staticmethod
    def _upsert_user(profile: dict[str, Any], token_payload: dict[str, Any]) -> User:
        """Create or refresh the local user from GitHub's profile + token payload."""
        github_id = int(profile["id"])
        username = str(profile["login"])

        user = User.objects.filter(github_id=github_id).first()
        if user is None:
            user = User(username=username)
        elif user.username != username:
            # GitHub logins can change; keep the local row but track the rename.
            user.username = username

        user.github_id = github_id
        user.email = profile.get("email") or ""
        user.full_name = (profile.get("name") or "")[:255]
        user.avatar_url = (profile.get("avatar_url") or "")[:500]
        user.store_github_token(
            access_token=str(token_payload["access_token"]),
            refresh_token=str(token_payload.get("refresh_token") or ""),
            scope=str(token_payload.get("scope") or ""),
            expires_in=token_payload.get("expires_in"),
        )
        user.set_unusable_password()
        user.save()
        return user

    def _redirect_with_error(self, code: str, message: str) -> Response:
        """Send the app back with a machine-readable failure."""
        return Response(
            status=status.HTTP_302_FOUND,
            headers={
                "Location": build_app_callback_url_from_error(code, message),
            },
        )


class HandoffExchangeView(APIView):
    """Step 3: trade the one-time code for JWTs."""

    permission_classes = (AllowAny,)
    authentication_classes = ()
    throttle_scope = "auth"

    @extend_schema(
        summary="Exchange handoff code for tokens",
        request=HandoffExchangeSerializer,
        responses={200: TokenPairSerializer},
    )
    def post(self, request: Any) -> Response:
        serializer = HandoffExchangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            user = OAuthHandoff.redeem(serializer.validated_data["code"])
        except OAuthHandoff.DoesNotExist:
            return Response(
                {
                    "error": {
                        "code": "invalid_handoff_code",
                        "message": "This sign-in link is invalid or has expired.",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({**issue_token_pair(user), "user": UserSerializer(user).data})


class MeView(APIView):
    """Current user profile and connection state."""

    permission_classes = (IsAuthenticated,)

    @extend_schema(summary="Current user", responses={200: UserSerializer})
    def get(self, request: Any) -> Response:
        return Response(UserSerializer(request.user).data)


class LogoutView(APIView):
    """Revoke the refresh token and forget the GitHub credentials."""

    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="Log out",
        request=LogoutSerializer,
        responses={200: None},
    )
    def post(self, request: Any) -> Response:
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        revoked = False
        refresh = serializer.validated_data.get("refresh", "")
        if refresh:
            try:
                RefreshToken(refresh).blacklist()
                revoked = True
            except TokenError:
                # An already-expired or bogus token still results in a logout;
                # the client just discards whatever it holds.
                revoked = False

        request.user.clear_github_token()
        request.user.save(
            update_fields=[
                "access_token_enc",
                "refresh_token_enc",
                "token_scope",
                "token_expires_at",
                "github_connected_at",
            ]
        )
        logger.info("user_logout", user_id=request.user.pk, revoked=revoked)
        return Response({"detail": "Signed out.", "revoked": revoked})


class GitHubDisconnectView(APIView):
    """Drop stored GitHub credentials but keep the account and its task history."""

    permission_classes = (IsAuthenticated,)

    @extend_schema(summary="Disconnect GitHub", responses={204: None})
    def delete(self, request: Any) -> Response:
        request.user.clear_github_token()
        request.user.save(
            update_fields=[
                "access_token_enc",
                "refresh_token_enc",
                "token_scope",
                "token_expires_at",
                "github_connected_at",
            ]
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


__all__: list[str] = [
    "GitHubAuthorizeStartView",
    "GitHubCallbackView",
    "GitHubDisconnectView",
    "HandoffExchangeView",
    "LogoutView",
    "MeView",
    "issue_token_pair",
]

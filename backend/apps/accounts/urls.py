"""URL routes for the accounts API."""

from __future__ import annotations

from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts import views

urlpatterns = [
    path("github/start", views.GitHubAuthorizeStartView.as_view(), name="github-start"),
    path("github/callback", views.GitHubCallbackView.as_view(), name="github-callback"),
    path("exchange", views.HandoffExchangeView.as_view(), name="handoff-exchange"),
    path("refresh", TokenRefreshView.as_view(), name="token-refresh"),
    path("me", views.MeView.as_view(), name="me"),
    path("logout", views.LogoutView.as_view(), name="logout"),
    path("github/disconnect", views.GitHubDisconnectView.as_view(), name="github-disconnect"),
]

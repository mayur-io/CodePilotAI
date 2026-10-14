"""Operations dashboard app configuration."""

from __future__ import annotations

from django.apps import AppConfig


class DashboardConfig(AppConfig):
    """Configuration for the operations dashboard app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.dashboard"
    label = "dashboard"
    verbose_name = "Operations dashboard"

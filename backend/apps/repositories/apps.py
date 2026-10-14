"""Repository and branch tracking."""

from __future__ import annotations

from django.apps import AppConfig


class RepositoriesConfig(AppConfig):
    """Configuration for the repositories app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.repositories"
    label = "repositories"
    verbose_name = "Repositories"

"""Django application configuration for the project-level config package."""

from __future__ import annotations

from django.apps import AppConfig


class ReadyHook(AppConfig):
    """Applies project-wide configuration after Django's app registry is ready."""

    name = "config"
    verbose_name = "CodePilotAI Config"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        """Route all logging through structlog."""
        from django.conf import settings

        from config.observability import configure_structlog

        configure_structlog(
            debug=settings.DEBUG,
            level=getattr(settings, "LOG_LEVEL", "INFO"),
        )

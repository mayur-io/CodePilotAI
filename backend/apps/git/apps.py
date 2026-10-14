from __future__ import annotations

from django.apps import AppConfig


class GitConfig(AppConfig):
    """Configuration for the local git plumbing app.

    This app has no models and no migrations; it exists to own every ``git``
    subprocess so credential handling and environment hardening live in one
    place.
    """

    name = "apps.git"
    label = "git_workspace"
    verbose_name = "Git workspace"

"""Agent task orchestration app."""

from __future__ import annotations

from django.apps import AppConfig


class AgentTasksConfig(AppConfig):
    """Configuration for the agent_tasks app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.agent_tasks"
    label = "agent_tasks"
    verbose_name = "Agent Tasks"

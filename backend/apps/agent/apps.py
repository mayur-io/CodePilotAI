from __future__ import annotations

from django.apps import AppConfig


class AgentConfig(AppConfig):
    """Configuration for the agent core.

    This app has no models: task state lives in ``apps.agent_tasks`` and git
    state lives in the worktree, so the graph itself is stateless and can be
    reconstructed on any worker.
    """

    name = "apps.agent"
    label = "agent"
    verbose_name = "Agent"

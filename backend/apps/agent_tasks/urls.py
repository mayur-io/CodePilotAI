"""URL routes for the agent tasks API."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.agent_tasks import views

router = DefaultRouter()
router.register("", views.AgentTaskViewSet, basename="agent-task")

urlpatterns = [
    path("", include(router.urls)),
    path(
        "<int:task_id>/stream",
        views.AgentTaskEventStreamView.as_view(),
        name="agent-task-stream",
    ),
]

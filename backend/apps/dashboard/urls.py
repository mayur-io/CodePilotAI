"""URL routes for the operations dashboard."""

from __future__ import annotations

from django.contrib.auth import views as auth_views
from django.urls import path

from apps.dashboard import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    path("tasks/", views.task_list, name="task-list"),
    path("tasks/<int:pk>/", views.task_detail, name="task-detail"),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="dashboard/login.html",
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]

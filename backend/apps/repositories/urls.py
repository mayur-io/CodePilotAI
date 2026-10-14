"""URL routes for the repositories API."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.repositories import views

router = DefaultRouter()
router.register("", views.RepositoryViewSet, basename="repository")

# One route per resource. `branches` is a viewset action, so it lives at
# `/repos/{id}/branches/`; an earlier manual `<int:repository_id>/branches` route
# pointed at a second view serving the same data under a second URL shape.
urlpatterns = [path("", include(router.urls))]

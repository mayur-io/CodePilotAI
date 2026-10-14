"""URL routes for the notifications API."""

from __future__ import annotations

from django.urls import path

from apps.notifications import views

# Explicit action-shaped routes rather than a router: a registration token is a
# credential-ish opaque string that does not belong in a URL path segment.
urlpatterns = [
    path("devices", views.DeviceRegisterView.as_view(), name="device-register"),
    path("devices/unregister", views.DeviceUnregisterView.as_view(), name="device-unregister"),
]

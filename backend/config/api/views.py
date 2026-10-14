"""Infrastructure views: health and readiness."""

from __future__ import annotations

from typing import ClassVar

from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    """Liveness/readiness probe.

    Reports database connectivity so a container orchestrator can distinguish
    "process is up" from "process is useful".
    """

    permission_classes = (AllowAny,)
    authentication_classes = ()
    throttle_classes: ClassVar[list] = []

    @extend_schema(
        summary="Health check",
        description="Returns service health including database connectivity.",
        responses={200: dict},
    )
    def get(self, request: object) -> Response:
        database_ok = True
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception:  # noqa: BLE001 - probe must never raise
            database_ok = False

        healthy = database_ok
        return Response(
            {"status": "ok" if healthy else "degraded", "database": database_ok},
            status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        )

"""Uniform API error envelope.

Every failure the mobile client can encounter is returned in the same shape so a
single Kotlin ``ApiError`` data class can parse all of them:

``{"error": {"code": "machine_readable_code", "message": "...", "details": {...}}}``
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from config.observability import get_logger

logger = get_logger(__name__)


def _envelope(code: str, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if details:
        body["details"] = details
    return {"error": body}


def copilot_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """DRF exception handler that emits the CodePilotAI error envelope."""
    from apps.agent_tasks.usage import DailyBudgetExceededError

    if isinstance(exc, Http404 | ObjectDoesNotExist):
        return Response(
            _envelope("not_found", "The requested resource does not exist."),
            status=status.HTTP_404_NOT_FOUND,
        )

    if isinstance(exc, DailyBudgetExceededError):
        return Response(
            _envelope(
                "daily_budget_exceeded",
                str(exc),
                {"spent": exc.spent, "limit": exc.limit},
            ),
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    if isinstance(exc, DjangoValidationError):
        return Response(
            _envelope("validation_error", "Request validation failed.", exc.message_dict),
            status=status.HTTP_400_BAD_REQUEST,
        )

    response = exception_handler(exc, context)

    if response is None:
        # Unhandled server error. Log with a traceback, never leak internals.
        request = context.get("request")
        logger.exception(
            "unhandled_api_exception",
            path=getattr(request, "path", None),
            exc_type=type(exc).__name__,
        )
        return Response(
            _envelope("internal_error", "Something went wrong on our side."),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    detail = response.data
    if isinstance(detail, dict) and set(detail.keys()) == {"detail"}:
        message = str(detail["detail"])
    elif isinstance(detail, list) and detail:
        message = str(detail[0])
    else:
        message = "Request failed."

    code = getattr(exc, "default_code", "error")
    return Response(_envelope(str(code), message, detail), status=response.status_code)

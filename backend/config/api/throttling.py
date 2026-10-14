"""Throttling rules for CodePilotAI.

Views opt into a named scope; the rate lives in ``REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']``.
Agent task creation is the expensive endpoint, so it gets its own tighter bucket.
"""

from __future__ import annotations

from rest_framework.throttling import ScopedRateThrottle


class ScopedBurstThrottle(ScopedRateThrottle):
    """Rate throttle driven by per-view ``throttle_scope``."""

    def allow_request(self, request: object, view: object) -> bool:
        """Allow the request unless the view declares no scope (unscoped == unlimited)."""
        scope = getattr(view, "throttle_scope", None)
        if not scope:
            return True
        return super().allow_request(request, view)

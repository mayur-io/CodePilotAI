"""Agent-layer failures.

Split by what the caller does next, not by where they were raised:

* :class:`GuardViolationError` and :class:`ToolError` are *for the model* -- they go
  back as a tool result so it can correct itself.
* :class:`ModelOutputError` is a malformed response; retry the call.
* :class:`BudgetError` and :class:`ProviderError` are terminal for the run.
"""

from __future__ import annotations


class AgentError(Exception):
    """Base class for every agent-layer failure."""


class GuardViolationError(AgentError):
    """A requested path is outside the sandbox or matches the secret policy."""

    def __init__(self, requested: str, reason: str) -> None:
        self.requested = requested
        self.reason = reason
        super().__init__(f"{requested!r}: {reason}")


class ToolError(AgentError):
    """A tool call failed for a reason the model can act on."""

    def __init__(self, name: str, message: str) -> None:
        self.name = name
        self.message = message
        super().__init__(f"{name}: {message}")


class ModelOutputError(AgentError):
    """The model returned something structurally unusable (bad JSON, no tool call)."""


class BudgetError(AgentError):
    """A run hit its token or wall-clock budget and must stop."""

    def __init__(self, kind: str, used: int, limit: int) -> None:
        self.kind = kind
        self.used = used
        self.limit = limit
        super().__init__(f"{kind} budget exhausted: {used} of {limit}")


class ProviderError(AgentError):
    """The LLM endpoint failed in a way that will not succeed on retry."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class ProviderRateLimitError(ProviderError):
    """HTTP 429. Carries the server's own backoff hint when it gave one."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message, status_code=429)


__all__: list[str] = [
    "AgentError",
    "BudgetError",
    "GuardViolationError",
    "ModelOutputError",
    "ProviderError",
    "ProviderRateLimitError",
    "ToolError",
]

"""Transport-agnostic types for talking to the model.

Kept separate from the Groq implementation so the graph and the tools can be
tested against a :class:`FakeProvider` without a socket.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from langchain_core.messages import BaseMessage

from apps.agent.errors import ModelOutputError

__all__: list[str] = [
    "Completion",
    "Provider",
    "ToolCall",
    "Usage",
]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One function call the model asked for, arguments already parsed."""

    id: str
    name: str
    arguments: dict[str, Any]

    def to_wire(self) -> dict[str, Any]:
        import json

        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": json.dumps(self.arguments)},
        }


@dataclass(frozen=True, slots=True)
class Usage:
    """Token accounting for a single completion."""

    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    #: Input tokens Groq served from its prompt cache. A subset of
    #: ``input_tokens``, billed at half the input rate.
    cached_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.reasoning_tokens

    @property
    def billable_tokens(self) -> int:
        """Groq bills ``completion_tokens``, which already includes reasoning."""
        return self.input_tokens + self.output_tokens

    @property
    def uncached_input_tokens(self) -> int:
        """Input tokens that missed the cache and bill at the full input rate."""
        return max(0, self.input_tokens - self.cached_tokens)

    @property
    def cache_hit_rate(self) -> float:
        """Fraction of input tokens served from the prompt cache (0.0 - 1.0)."""
        if self.input_tokens <= 0:
            return 0.0
        return min(1.0, self.cached_tokens / self.input_tokens)


@dataclass(frozen=True, slots=True)
class Completion:
    """A parsed model response."""

    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    reasoning: str = ""
    finish_reason: str = ""
    usage: Usage = field(default_factory=Usage)
    model: str = ""

    @property
    def wants_tool_call(self) -> bool:
        return bool(self.tool_calls)

    @property
    def only_tool_call(self) -> ToolCall:
        """The single tool call, which is all gpt-oss can emit per step.

        Parallel tool calls are unsupported on this model (Task.md §3), so a
        response carrying two is a protocol violation rather than a feature to
        support -- and silently honouring the first would drop work the model
        believes it requested.
        """
        if len(self.tool_calls) != 1:
            raise ModelOutputError(f"expected exactly one tool call, got {len(self.tool_calls)}")
        return self.tool_calls[0]


@runtime_checkable
class Provider(Protocol):
    """Minimal surface the graph depends on."""

    def complete(
        self,
        messages: Sequence[BaseMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        reasoning_effort: str | None = None,
        max_tokens: int | None = None,
    ) -> Completion: ...

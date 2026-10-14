"""gpt-oss message normalisation and response parsing.

Two directions of traffic, both strict:

* **Outbound** -- :func:`normalize` turns the stored thread into something an
  OpenAI-compatible endpoint will accept. gpt-oss rejects a surprising number
  of things real clients accumulate: helper messages with empty content,
  consecutive same-role messages, tool messages that reference a call the model
  never made, and an assistant turn that still carries tool calls when the run
  was aborted before the tools answered.
* **Inbound** -- :func:`parse_completion` reads the response the way Groq
  actually sends it (``message.reasoning`` -- Task.md §3) and turns it into
  :class:`~apps.agent.providers.base.Completion`. Malformed JSON in a tool
  argument raises :class:`~apps.agent.errors.ModelOutputError`, which the
  runner treats as "ask the model to fix its output", not as a crash.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from apps.agent.errors import ModelOutputError
from apps.agent.providers.base import Completion, ToolCall, Usage

__all__: list[str] = [
    "normalize",
    "parse_completion",
    "to_wire",
    "tool_calls_from_ai",
]


def normalize(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """Make a stored thread safe to resend to the model.

    Idempotent -- feeding its own output back in changes nothing -- and lossless
    for everything a client would want to retry.
    """
    result: list[BaseMessage] = []
    previous: BaseMessage | None = None

    for message in messages:
        if _is_empty(message):
            continue
        previous = result[-1] if result else None
        if _can_merge(previous, message):
            result[-1] = _merge(previous, message)  # type: ignore[arg-type]
            continue
        result.append(message)

    result = _drop_orphan_tool_messages(result)
    return _close_dangling_tool_calls(result)


def to_wire(messages: Sequence[BaseMessage]) -> list[dict[str, Any]]:
    """Serialize a normalized thread to OpenAI wire format."""
    wire: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, SystemMessage):
            wire.append({"role": "system", "content": message.content})
        elif isinstance(message, HumanMessage):
            wire.append({"role": "user", "content": message.content})
        elif isinstance(message, ToolMessage):
            wire.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": message.content,
                }
            )
        elif isinstance(message, AIMessage):
            entry: dict[str, Any] = {"role": "assistant"}
            if message.content:
                entry["content"] = message.content
            tool_calls = tool_calls_from_ai(message)
            if tool_calls:
                entry["tool_calls"] = [call.to_wire() for call in tool_calls]
            wire.append(entry)
        else:
            wire.append({"role": "assistant", "content": str(message.content)})
    return wire


def parse_completion(payload: dict[str, Any]) -> Completion:
    """Parse one ``chat/completions`` response body into a :class:`Completion`."""
    try:
        choice = payload["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelOutputError(
            f"response has no choices[0].message: {_safe_summary(payload)}"
        ) from exc

    content = message.get("content") or ""
    if not isinstance(content, str):
        content = ""
    reasoning = message.get("reasoning") or message.get("reasoning_content") or ""

    tool_calls = tuple(_parse_tool_call(item) for item in message.get("tool_calls") or [])

    usage_payload = payload.get("usage") or {}
    details = usage_payload.get("completion_tokens_details") or {}
    prompt_details = usage_payload.get("prompt_tokens_details") or {}
    usage = Usage(
        input_tokens=int(usage_payload.get("prompt_tokens") or 0),
        output_tokens=int(usage_payload.get("completion_tokens") or 0),
        reasoning_tokens=int(details.get("reasoning_tokens") or 0),
        cached_tokens=int(prompt_details.get("cached_tokens") or 0),
    )

    return Completion(
        content=content,
        tool_calls=tool_calls,
        reasoning=reasoning,
        finish_reason=str(choice.get("finish_reason") or ""),
        usage=usage,
        model=str(payload.get("model") or ""),
    )


# ── outbound helpers ─────────────────────────────────────────────────────────


def _is_empty(message: BaseMessage) -> bool:
    if isinstance(message, ToolMessage):
        return False
    if isinstance(message, AIMessage) and (message.tool_calls or tool_calls_from_ai(message)):
        return False
    content = message.content
    return not content or not str(content).strip()


def _can_merge(previous: BaseMessage | None, message: BaseMessage) -> bool:
    if previous is None:
        return False
    if isinstance(message, (ToolMessage, AIMessage)) or isinstance(
        previous, (ToolMessage, AIMessage)
    ):
        return False
    return type(previous) is type(message)


def _merge(first: BaseMessage, second: BaseMessage) -> BaseMessage:
    joined = str(first.content).strip() + "\n\n" + str(second.content).strip()
    cls = type(first)
    return cls(content=joined)


def _drop_orphan_tool_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Remove ToolMessages whose call was never made in this thread.

    gpt-oss (and every OpenAI-compatible endpoint) rejects a ``role=tool``
    message whose ``tool_call_id`` does not match a preceding assistant call.
    Orphans appear when a run is pruned for budget or interrupted mid-turn.
    """
    produced: set[str] = set()
    result: list[BaseMessage] = []
    for message in messages:
        if isinstance(message, AIMessage):
            produced.update(call.id for call in tool_calls_from_ai(message))
        elif isinstance(message, ToolMessage) and message.tool_call_id not in produced:
            continue
        result.append(message)
    return result


def _close_dangling_tool_calls(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Strip tool calls from a trailing assistant turn that never got answered.

    Without this, a thread that ended mid-loop can never be replayed: the model
    would be asked to continue from an unresolved tool call.
    """
    if not messages or not isinstance(messages[-1], AIMessage):
        return messages

    last = messages[-1]
    calls = tool_calls_from_ai(last)
    if not calls:
        return messages

    return [
        *messages[:-1],
        AIMessage(content=last.content or "", tool_calls=[]),
    ]


def tool_calls_from_ai(message: AIMessage) -> list[ToolCall]:
    """Read tool calls from any shape they arrive in.

    ``langchain-core`` normalises ``tool_calls=[{id, name, args}]`` into its own
    pydantic ``ToolCall`` model; wire-format dicts carry ``{id, function:
    {name, arguments}}``. Accept both -- the debounce point is the model, not
    the in-memory representation.
    """
    calls: list[ToolCall] = []
    for call in (
        getattr(message, "tool_calls", None)
        or getattr(message, "additional_kwargs", {}).get("tool_calls")
        or []
    ):
        if isinstance(call, ToolCall):
            calls.append(call)
            continue
        if isinstance(call, dict):
            function = call.get("function")
            if isinstance(function, dict):
                calls.append(
                    ToolCall(
                        id=str(call.get("id") or ""),
                        name=str(function.get("name") or ""),
                        arguments=_coerce_arguments(function.get("arguments")),
                    )
                )
            else:
                calls.append(
                    ToolCall(
                        id=str(call.get("id") or ""),
                        name=str(call.get("name") or ""),
                        arguments=_coerce_arguments(call.get("args")),
                    )
                )
            continue
        # langchain-core's pydantic ToolCall model.
        calls.append(
            ToolCall(
                id=str(getattr(call, "id", "") or ""),
                name=str(getattr(call, "name", "") or ""),
                arguments=_coerce_arguments(getattr(call, "args", {})),
            )
        )
    return calls


def _coerce_arguments(raw: Any) -> dict[str, Any]:
    """Tool arguments arrive as a JSON string from the endpoint and as dicts
    when the message was constructed locally; accept both."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        import json

        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise ModelOutputError(
                f"tool arguments are not valid JSON: {_safe_summary(raw)}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ModelOutputError(f"tool arguments must be an object, got {type(parsed).__name__}")
        return parsed
    return {}


def _parse_tool_call(item: Any) -> ToolCall:
    if not isinstance(item, dict):
        raise ModelOutputError("tool_calls entry is not an object")
    function = item.get("function") or {}
    return ToolCall(
        id=str(item.get("id") or ""),
        name=str(function.get("name") or ""),
        arguments=_coerce_arguments(function.get("arguments")),
    )


def _safe_summary(value: Any) -> str:
    summary = str(value)
    return summary[:300] + ("..." if len(summary) > 300 else "")

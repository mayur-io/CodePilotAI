"""Tests for the LLM layer: gpt-oss message normalisation and the Groq transport.

``pytest-httpx`` raises on any unregistered request, which is exactly the
guarantee this module needs: a provider bug that lets a request escape to the
live API would fail the test loudly instead of silently billing tokens.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from apps.agent.errors import ModelOutputError, ProviderError, ProviderRateLimitError
from apps.agent.providers.base import ToolCall
from apps.agent.providers.groq import GroqProvider
from apps.agent.providers.normalize import normalize, parse_completion, to_wire

BASE_URL = "https://api.groq.com/openai/v1"
MODEL = "openai/gpt-oss-120b"
API_KEY = "gk_test_secret_key_do_not_echo"


def _no_op_sleep(_: float) -> None:
    return None


def make_provider(sleep: Any = None, **overrides: Any) -> GroqProvider:
    kwargs: dict[str, Any] = {
        "api_key": API_KEY,
        "base_url": BASE_URL,
        "model": MODEL,
        "timeout_seconds": 30,
        "max_retries": 2,
        "temperature": 0.6,
        "top_p": 0.95,
        "default_reasoning_effort": "medium",
    }
    if sleep is None:
        sleep = _no_op_sleep
    kwargs["sleep"] = sleep
    kwargs.update(overrides)
    return GroqProvider(**kwargs)


def completion_payload(
    *,
    content: str = "ok",
    tool_calls: list[dict[str, Any]] | None = None,
    reasoning: str = "think",
    finish_reason: str = "stop",
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
    reasoning_tokens: int = 5,
    cached_tokens: int = 0,
    model: str = MODEL,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content, "reasoning": reasoning}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-1",
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {"cached_tokens": cached_tokens},
            "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
        },
    }


def tool_call(name: str = "read_file", arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": "call_1",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments or {"path": "README.md"})},
    }


# ── parse_completion ─────────────────────────────────────────────────────────


class TestParseCompletion:
    def test_plain_text_completion(self) -> None:
        completion = parse_completion(completion_payload())
        assert completion.content == "ok"
        assert completion.reasoning == "think"
        assert completion.finish_reason == "stop"
        assert completion.usage.input_tokens == 10
        assert completion.usage.output_tokens == 20
        assert completion.usage.reasoning_tokens == 5
        assert completion.usage.cached_tokens == 0
        assert completion.tool_calls == ()

    def test_prompt_cache_tokens_are_parsed(self) -> None:
        completion = parse_completion(completion_payload(prompt_tokens=100, cached_tokens=64))
        assert completion.usage.cached_tokens == 64
        assert completion.usage.uncached_input_tokens == 36
        assert completion.usage.cache_hit_rate == pytest.approx(0.64)

    def test_tool_calls_are_parsed_from_json_strings(self) -> None:
        completion = parse_completion(
            completion_payload(content="", tool_calls=[tool_call()], finish_reason="tool_calls")
        )
        (call,) = completion.tool_calls
        assert isinstance(call, ToolCall)
        assert call.id == "call_1"
        assert call.name == "read_file"
        assert call.arguments == {"path": "README.md"}

    def test_malformed_tool_arguments_raise(self) -> None:
        bad = tool_call(arguments={"path": "x"})
        bad["function"]["arguments"] = "{ not json"
        with pytest.raises(ModelOutputError, match="not valid JSON"):
            parse_completion(completion_payload(content="", tool_calls=[bad]))

    def test_non_object_tool_arguments_raise(self) -> None:
        bad = tool_call(arguments={"path": "x"})
        bad["function"]["arguments"] = "[1, 2]"
        with pytest.raises(ModelOutputError, match="must be an object"):
            parse_completion(completion_payload(content="", tool_calls=[bad]))

    def test_missing_choices_raise_with_outline(self) -> None:
        with pytest.raises(ModelOutputError, match="choices"):
            parse_completion({"id": "x", "model": MODEL})

    def test_pre_parsed_dict_arguments_are_accepted(self) -> None:
        raw = tool_call(arguments={"path": "README.md"})
        raw["function"]["arguments"] = {"path": "README.md"}  # type: ignore[assignment]
        completion = parse_completion(completion_payload(content="", tool_calls=[raw]))
        assert completion.tool_calls[0].arguments == {"path": "README.md"}


# ── normalize / to_wire ──────────────────────────────────────────────────────


class TestNormalize:
    def test_empty_messages_are_dropped(self) -> None:
        result = normalize([SystemMessage(content="sys"), HumanMessage(content="  ")])
        assert [(type(m).__name__, m.content) for m in result] == [("SystemMessage", "sys")]

    def test_consecutive_same_role_messages_merge(self) -> None:
        result = normalize(
            [
                SystemMessage(content="a"),
                SystemMessage(content="b"),
                HumanMessage(content="task"),
                HumanMessage(content="more"),
            ]
        )
        assert len(result) == 2
        assert "a\n\nb" in result[0].content  # type: ignore[union-attr]
        assert "task\n\nmore" in result[1].content  # type: ignore[union-attr]

    def test_assistant_tool_turns_are_not_merged(self) -> None:
        first = AIMessage(content="", tool_calls=[{"id": "c1", "name": "read_file", "args": {}}])
        second = AIMessage(content="found it")
        result = normalize([HumanMessage(content="go"), first, second])
        assert len(result) == 3

    def test_orphan_tool_message_is_dropped(self) -> None:
        orphan = ToolMessage(content="result", tool_call_id="never_made")
        result = normalize([SystemMessage(content="sys"), HumanMessage(content="go"), orphan])
        assert all(not isinstance(m, ToolMessage) for m in result)

    def test_matched_tool_message_is_kept(self) -> None:
        assistant = AIMessage(
            content="", tool_calls=[{"id": "c1", "name": "read_file", "args": {"path": "x"}}]
        )
        tool = ToolMessage(content="file content", tool_call_id="c1")
        result = normalize(
            [SystemMessage(content="sys"), HumanMessage(content="go"), assistant, tool]
        )
        assert len(result) == 4
        assert isinstance(result[-1], ToolMessage)

    def test_dangling_tool_calls_are_closed(self) -> None:
        dangling = AIMessage(content="", tool_calls=[{"id": "c1", "name": "read_file", "args": {}}])
        result = normalize([SystemMessage(content="sys"), HumanMessage(content="go"), dangling])
        assert isinstance(result[-1], AIMessage)
        assert result[-1].tool_calls == []

    def test_normalize_is_idempotent(self) -> None:
        messages = [
            SystemMessage(content="sys"),
            HumanMessage(content="task"),
            AIMessage(content="", tool_calls=[{"id": "c1", "name": "read_file", "args": {}}]),
            ToolMessage(content="x", tool_call_id="c1"),
        ]
        once = normalize(messages)
        twice = normalize(once)
        assert [type(m) for m in once] == [type(m) for m in twice]
        assert [m.content for m in once] == [m.content for m in twice]


class TestToWire:
    def test_basic_roles(self) -> None:
        wire = to_wire(
            [
                SystemMessage(content="sys"),
                HumanMessage(content="task"),
                ToolMessage(content="x", tool_call_id="c1"),
            ]
        )
        assert wire[0] == {"role": "system", "content": "sys"}
        assert wire[1] == {"role": "user", "content": "task"}
        assert wire[2]["role"] == "tool"
        assert wire[2]["tool_call_id"] == "c1"

    def test_assistant_tool_calls_serialise_arguments_as_json(self) -> None:
        assistant = AIMessage(
            content="",
            tool_calls=[{"id": "c1", "name": "read_file", "args": {"path": "a b.py"}}],
        )
        (entry,) = to_wire([assistant])
        assert entry["role"] == "assistant"
        assert "content" not in entry
        assert entry["tool_calls"][0]["function"]["arguments"] == '{"path": "a b.py"}'


# ── GroqProvider transport ───────────────────────────────────────────────────


class TestGroqConstruction:
    def test_missing_api_key_is_rejected(self) -> None:
        with pytest.raises(ProviderError, match="API key is not configured"):
            make_provider(api_key="")

    def test_context_manager_closes_the_client(self) -> None:
        with make_provider() as provider:
            assert provider is not None

    def test_default_reasoning_effort_is_used_when_not_given(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(json=completion_payload())
        provider = make_provider()
        provider.complete([SystemMessage(content="sys")])
        body = json.loads(httpx_mock.get_requests()[0].content)
        assert body["reasoning_effort"] == "medium"


class TestGroqRequestBody:
    def test_happy_path_request_shape(self, httpx_mock: Any, sent_json: Any) -> None:
        httpx_mock.add_response(json=completion_payload())
        provider = make_provider()
        completion = provider.complete(
            [SystemMessage(content="sys"), HumanMessage(content="task")],
            tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
            reasoning_effort="high",
            max_tokens=512,
        )

        body = sent_json()
        assert body["model"] == MODEL
        assert body["include_reasoning"] is True
        assert body["reasoning_effort"] == "high"
        assert body["max_tokens"] == 512
        assert body["tools"][0]["function"]["name"] == "read_file"
        assert body["tool_choice"] == "auto"
        assert "stop" not in body
        assert body["messages"][0]["role"] == "system"

        assert completion.model == MODEL
        assert completion.reasoning == "think"

    def test_request_does_not_paginate_or_stream(self, httpx_mock: Any, sent_json: Any) -> None:
        httpx_mock.add_response(json=completion_payload())
        make_provider().complete([SystemMessage(content="sys")])
        body = sent_json()
        assert "stream" not in body
        assert "stop" not in body


class TestGroqRetries:
    def test_429_then_success(self, httpx_mock: Any) -> None:
        sleeps: list[float] = []
        httpx_mock.add_response(status_code=429, json={"error": {"message": "slow down"}})
        httpx_mock.add_response(json=completion_payload())
        provider = make_provider(sleep=sleeps.append)

        completion = provider.complete([SystemMessage(content="sys")])

        assert completion.content == "ok"
        assert len(httpx_mock.get_requests()) == 2

    def test_429_respects_retry_after(self, httpx_mock: Any) -> None:
        sleeps: list[float] = []
        httpx_mock.add_response(
            status_code=429, headers={"Retry-After": "2.5"}, json={"error": {"message": "quota"}}
        )
        httpx_mock.add_response(json=completion_payload())
        make_provider(sleep=sleeps.append).complete([SystemMessage(content="sys")])
        assert sleeps == [2.5]

    def test_429_exhausted_raises_rate_limit_error(self, httpx_mock: Any) -> None:
        for _ in range(3):
            httpx_mock.add_response(status_code=429, json={"error": {"message": "no"}})
        with pytest.raises(ProviderRateLimitError) as excinfo:
            make_provider(sleep=lambda _: None).complete([SystemMessage(content="sys")])
        assert excinfo.value.retry_after is None
        assert excinfo.value.status_code == 429

    def test_500_then_success(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(status_code=500, text="boom")
        httpx_mock.add_response(json=completion_payload())
        completion = make_provider(sleep=lambda _: None).complete([SystemMessage(content="sys")])
        assert completion.content == "ok"

    def test_503_exhausted_raises_provider_error(self, httpx_mock: Any) -> None:
        for _ in range(3):
            httpx_mock.add_response(status_code=503, text="unavailable")
        with pytest.raises(ProviderError) as excinfo:
            make_provider(sleep=lambda _: None).complete([SystemMessage(content="sys")])
        assert excinfo.value.status_code == 503

    def test_deterministic_errors_are_never_retried(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(status_code=401, json={"error": {"message": "bad key"}})
        with pytest.raises(ProviderError) as excinfo:
            make_provider(sleep=lambda _: None).complete([SystemMessage(content="sys")])
        assert excinfo.value.status_code == 401
        assert len(httpx_mock.get_requests()) == 1

    def test_transport_error_is_retried(self, httpx_mock: Any) -> None:
        httpx_mock.add_exception(httpx.ConnectError("connection refused"))
        httpx_mock.add_response(json=completion_payload())
        completion = make_provider(sleep=lambda _: None).complete([SystemMessage(content="sys")])
        assert completion.content == "ok"

    def test_api_key_is_scrubbed_from_error_bodies(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(
            status_code=500, text=f"proxy echoed {API_KEY} back", is_reusable=True
        )
        with pytest.raises(ProviderError) as excinfo:
            make_provider(sleep=lambda _: None).complete([SystemMessage(content="sys")])
        assert API_KEY not in str(excinfo.value)
        assert "***" in str(excinfo.value)

    def test_non_json_success_body_raises(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(status_code=200, text="not json")
        with pytest.raises(ProviderError, match="non-JSON"):
            make_provider().complete([SystemMessage(content="sys")])

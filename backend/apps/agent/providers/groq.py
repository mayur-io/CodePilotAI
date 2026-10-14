"""Groq ``gpt-oss-120b``, called over httpx and normalised in this repo.

Why not ``langchain-openai``? Its own source is explicit that reasoning fields
(``reasoning_content`` / ``reasoning_details``) are *not extracted* and tells
you to subclass. Reasoning is a first-class, user-visible feature here -- it is
streamed to the app as the "thinking" trace -- so the transport is a thin,
explicit httpx client instead (same stack as ``apps.github.client``), and the
retry policy mirrors the one already used there: 429/5xx back off, deterministic
errors do not.

Constraints honoured from Task.md §3: ``include_reasoning: true`` with the trace
read from ``message.reasoning``; ``reasoning_effort`` limited to
low|medium|high and set explicitly per call; no ``stop`` sequences; exactly one
tool call per step.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from apps.agent.errors import ProviderError, ProviderRateLimitError
from apps.agent.providers.base import Completion
from apps.agent.providers.normalize import parse_completion, to_wire
from config.observability import get_logger

logger = get_logger(__name__)

__all__: list[str] = ["GroqProvider"]

#: Retryable classes and statuses. 400/401/404 are deterministic and never
#: retried (the callers' logs are not going to fix an invalid request).
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_RETRYABLE_EXCEPTIONS = (httpx.TimeoutException, httpx.TransportError)
_BASE_BACKOFF = 0.8


class GroqProvider:
    """OpenAI-compatible chat completions client for Groq.

    ``sleep`` and ``transport`` are injectable so tests can avoid real backoff
    delays and real sockets respectively; production uses the defaults.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_retries: int,
        temperature: float,
        top_p: float,
        default_reasoning_effort: str = "medium",
        sleep: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ProviderError("Groq API key is not configured")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout_seconds
        self.max_retries = max_retries
        self.temperature = temperature
        self.top_p = top_p
        self.default_reasoning_effort = default_reasoning_effort
        self._sleep = sleep
        self._client = httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GroqProvider:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def complete(
        self,
        messages: Sequence[Any],
        *,
        tools: list[dict[str, Any]] | None = None,
        reasoning_effort: str | None = None,
        max_tokens: int | None = None,
    ) -> Completion:
        """Send one chat completion request, retrying only what is retryable."""
        wire_messages = to_wire(messages)

        body: dict[str, Any] = {
            "model": self.model,
            "messages": wire_messages,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "include_reasoning": True,
            "reasoning_effort": reasoning_effort or self.default_reasoning_effort,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if max_tokens is not None:
            body["max_tokens"] = max_tokens

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.post(
                    f"{self.base_url}/chat/completions",
                    json=body,
                )
            except _RETRYABLE_EXCEPTIONS as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
                _backoff(self._sleep, attempt)
                continue

            if response.status_code == 429:
                retry_after = _retry_after(response)
                if attempt == self.max_retries:
                    api_key_scrubbed = _scrub_key(str(response.text or "")[:500], self.api_key)
                    raise ProviderRateLimitError(
                        f"Groq rate limited: {api_key_scrubbed}", retry_after=retry_after
                    )
                if retry_after is not None:
                    self._sleep(min(retry_after, 30.0))
                else:
                    _backoff(self._sleep, attempt)
                last_error = ProviderRateLimitError("Groq rate limited", retry_after=retry_after)
                continue

            if response.status_code in _RETRYABLE_STATUS:
                if attempt == self.max_retries:
                    _log_failure("groq_http_failed", response)
                    raise _http_error(response, self.api_key)
                _backoff(self._sleep, attempt)
                last_error = _http_error(response, self.api_key)
                continue

            if response.status_code != 200:
                _log_failure("groq_http_rejected", response)
                raise _http_error(response, self.api_key)

            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderError(f"Groq returned non-JSON body: {exc}") from exc

            completion = parse_completion(payload)
            logger.debug(
                "groq_completion",
                model=self.model,
                input_tokens=completion.usage.input_tokens,
                output_tokens=completion.usage.output_tokens,
                reasoning_tokens=completion.usage.reasoning_tokens,
                cached_tokens=completion.usage.cached_tokens,
                cache_hit_rate=round(completion.usage.cache_hit_rate, 3),
                tool_calls=len(completion.tool_calls),
                finish_reason=completion.finish_reason,
            )
            return completion

        if isinstance(last_error, ProviderError):
            raise last_error
        raise ProviderError(
            f"Groq request failed after {self.max_retries + 1} attempts: {last_error}"
        )


def _backoff(sleep: Callable[[float], None], attempt: int) -> float:
    delay = _BASE_BACKOFF * (2**attempt)
    sleep(delay)
    return delay


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 10.0


def _http_error(response: httpx.Response, api_key: str) -> ProviderError:
    # Never let a key echoed back by a proxy land in an exception message.
    body = _scrub_key(str(response.text or "")[:800], api_key)
    if response.status_code == 429:
        return ProviderRateLimitError(f"Groq rate limited: {body}")
    return ProviderError(
        f"Groq HTTP {response.status_code}: {body}", status_code=response.status_code
    )


def _scrub_key(text: str, api_key: str) -> str:
    if not api_key:
        return text
    return text.replace(api_key, "***")


def _log_failure(event: str, response: httpx.Response) -> None:
    logger.warning(
        event,
        status=response.status_code,
        response_snippet=str(response.text or "")[:300],
    )

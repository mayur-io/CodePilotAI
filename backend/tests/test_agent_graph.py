"""Tests for the LangGraph tool loop: the shape gpt-oss requires.

The loop is where the protocol constraints land (Task.md §3): one tool call per
step, a deterministic round cap, and budgets checked before every model call
and every tool execution. These tests drive a :class:`ScriptedProvider` so the
loop mechanics are exercised without a socket.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from apps.agent.budget import Budget
from apps.agent.errors import BudgetError, ModelOutputError
from apps.agent.graph import (
    DEFAULT_PER_CALL_TOKEN_CAP,
    ExecOutcome,
    GraphState,
    build_agent_graph,
    initial_state,
)
from apps.agent.providers.base import Completion, ToolCall, Usage
from apps.agent.providers.normalize import tool_calls_from_ai
from apps.agent.tools import ToolRegistry


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    return root


def make_executor(registry: ToolRegistry) -> Any:
    def execute(call: ToolCall) -> ExecOutcome:
        return ExecOutcome(result=registry.run(call.name, call.arguments))

    return execute


def big_budget() -> Budget:
    return Budget(token_limit=1_000_000, wall_clock_seconds=120)


def build_graph(
    provider: Any,
    registry: ToolRegistry,
    budget: Budget,
    *,
    reasoning_effort: str = "low",
    max_tool_calls: int = 25,
) -> Any:
    return build_agent_graph(
        provider=provider,
        tool_schemas=registry.to_openai(),
        executor=make_executor(registry),
        budget=budget,
        reasoning_effort=reasoning_effort,
        max_tool_calls=max_tool_calls,
    )


class TestToolLoop:
    def test_tool_then_answer(
        self,
        tmp_path: Path,
        scripted_provider: Any,
        tool_completion: Any,
        answer_completion: Any,
    ) -> None:
        registry = ToolRegistry(make_repo(tmp_path))
        provider = scripted_provider(
            [tool_completion("read_file", {"path": "src/app.py"}), answer_completion("One file.")]
        )
        budget = big_budget()
        final = build_graph(provider, registry, budget).invoke(
            initial_state(
                system=SystemMessage(content="sys"),
                task=HumanMessage(content="task"),
                max_tool_calls=25,
            )
        )

        messages = final["messages"]
        assert len(messages) == 5
        assert isinstance(messages[0], SystemMessage)
        assert isinstance(messages[1], HumanMessage)
        assert isinstance(messages[2], AIMessage)
        assert tool_calls_from_ai(messages[2])[0].name == "read_file"
        assert isinstance(messages[3], ToolMessage)
        assert "app.py" in messages[3].content
        assert isinstance(messages[4], AIMessage)
        assert messages[4].content == "One file."
        assert final["stopped_reason"] == ""
        # Both the model calls and the single tool execution are accounted for.
        assert budget.llm_calls == 2
        assert budget.tool_calls == 1

    def test_model_ends_without_tools(
        self,
        tmp_path: Path,
        scripted_provider: Any,
        answer_completion: Any,
    ) -> None:
        registry = ToolRegistry(make_repo(tmp_path))
        provider = scripted_provider([answer_completion("Nothing to look at.")])
        budget = big_budget()
        final = build_graph(provider, registry, budget).invoke(
            initial_state(system=SystemMessage(content="s"), task=HumanMessage(content="t"))
        )
        assert len(final["messages"]) == 3
        assert final["messages"][-1].content == "Nothing to look at."
        assert budget.tool_calls == 0

    def test_provider_receives_tools_effort_and_token_cap(
        self,
        tmp_path: Path,
        scripted_provider: Any,
        answer_completion: Any,
    ) -> None:
        registry = ToolRegistry(make_repo(tmp_path))
        provider = scripted_provider([answer_completion("done")])
        budget = big_budget()
        build_graph(provider, registry, budget, reasoning_effort="low").invoke(
            initial_state(system=SystemMessage(content="s"), task=HumanMessage(content="t"))
        )
        kwargs = provider.kwargs[0]
        assert kwargs["reasoning_effort"] == "low"
        assert kwargs["max_tokens"] == DEFAULT_PER_CALL_TOKEN_CAP
        names = {entry["function"]["name"] for entry in kwargs["tools"]}
        assert names == {"list_dir", "read_file", "search_code", "read_repo_instructions"}

    def test_parallel_tool_calls_are_a_protocol_violation(
        self,
        tmp_path: Path,
        scripted_provider: Any,
    ) -> None:
        registry = ToolRegistry(make_repo(tmp_path))
        parallel = Completion(
            content="",
            tool_calls=(
                ToolCall(id="a", name="read_file", arguments={"path": "README.md"}),
                ToolCall(id="b", name="list_dir", arguments={"path": "."}),
            ),
            usage=Usage(input_tokens=5, output_tokens=5),
        )
        provider = scripted_provider([parallel])
        with pytest.raises(ModelOutputError, match="parallel"):
            build_graph(provider, registry, big_budget()).invoke(
                initial_state(system=SystemMessage(content="s"), task=HumanMessage(content="t"))
            )

    def test_round_cap_stops_at_known_cost(
        self,
        tmp_path: Path,
        scripted_provider: Any,
        tool_completion: Any,
    ) -> None:
        registry = ToolRegistry(make_repo(tmp_path))
        runaway = [tool_completion("list_dir", {"path": "."}) for _ in range(10)]
        provider = scripted_provider(runaway)
        graph = build_graph(provider, registry, big_budget(), max_tool_calls=2)
        final = graph.invoke(
            initial_state(
                system=SystemMessage(content="s"),
                task=HumanMessage(content="t"),
                max_tool_calls=2,
            )
        )
        assert final["stopped_reason"] == "tool_calls_exhausted"
        # Two rounds consumed the cap; the provider was never over-drawn.
        assert len(provider.calls) == 2
        assert budget_tool_calls_used(final) == 2

    def test_budget_exhaustion_aborts_before_the_next_call(
        self,
        tmp_path: Path,
        scripted_provider: Any,
        tool_completion: Any,
    ) -> None:
        registry = ToolRegistry(make_repo(tmp_path))
        provider = scripted_provider(
            [
                tool_completion("read_file", {"path": "README.md"}),
                tool_completion("read_file", {"path": "README.md"}),
            ]
        )
        # Each scripted completion reports 10 tokens; a limit of 14 means the
        # second completion lands, and the third model call must never start.
        budget = Budget(token_limit=14, wall_clock_seconds=120)
        with pytest.raises(BudgetError, match="token budget exhausted"):
            build_graph(provider, registry, budget).invoke(
                initial_state(system=SystemMessage(content="s"), task=HumanMessage(content="t"))
            )
        assert len(provider.calls) == 2

    def test_reasoning_trail_accumulates_in_order(
        self,
        tmp_path: Path,
        scripted_provider: Any,
        tool_completion: Any,
        answer_completion: Any,
    ) -> None:
        registry = ToolRegistry(make_repo(tmp_path))
        provider = scripted_provider(
            [
                tool_completion("read_file", {"path": "src/app.py"}, reasoning="look here"),
                answer_completion("understood", reasoning="then answer"),
            ]
        )
        final = build_graph(provider, registry, big_budget()).invoke(
            initial_state(system=SystemMessage(content="s"), task=HumanMessage(content="t"))
        )
        assert final["reasoning"] == ["look here", "then answer"]


def budget_tool_calls_used(final_state: GraphState) -> int:
    """Count tool executions from the message trail alone."""
    return sum(1 for message in final_state["messages"] if isinstance(message, ToolMessage))

"""The explore phase: let the model look before it proposes.

A thin wrapper over the shared tool loop: the model gets the four read-only
tools (list_dir / read_file / search_code / read_repo_instructions) at low
reasoning effort and is done the moment it answers without a tool call. It
cannot write anything, so this phase needs no controller -- the registry's
:meth:`~apps.agent.tools.ToolRegistry.run` is the whole executor.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from apps.agent.budget import Budget
from apps.agent.graph import (
    DEFAULT_MAX_TOOL_CALLS,
    ExecOutcome,
    build_agent_graph,
    initial_state,
)
from apps.agent.providers.base import Provider, ToolCall
from apps.agent.tools import ToolRegistry

__all__: list[str] = [
    "ExploreResult",
    "run_explore",
]


@dataclass(frozen=True, slots=True)
class ExploreResult:
    """The explored conversation and the reasoning trail, for the record."""

    messages: list[BaseMessage]
    reasoning: list[str]
    stopped_reason: str


def run_explore(
    *,
    provider: Provider,
    registry: ToolRegistry,
    system: SystemMessage,
    task: HumanMessage,
    budget: Budget,
    reasoning_effort: str = "low",
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
) -> ExploreResult:
    """Run the read-only exploration loop to completion or the loop cap."""

    def execute(call: ToolCall) -> ExecOutcome:
        result = registry.run(call.name, call.arguments)
        return ExecOutcome(result=result)

    graph = build_agent_graph(
        provider=provider,
        tool_schemas=registry.to_openai(),
        executor=execute,
        budget=budget,
        reasoning_effort=reasoning_effort,
        max_tool_calls=max_tool_calls,
    )
    final = graph.invoke(
        initial_state(system=system, task=task, max_tool_calls=max_tool_calls),
        config={"recursion_limit": max_tool_calls * 2 + 8},
    )
    return ExploreResult(
        messages=final["messages"],
        reasoning=final.get("reasoning", []),
        stopped_reason=final.get("stopped_reason", ""),
    )

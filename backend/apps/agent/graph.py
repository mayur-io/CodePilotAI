"""LangGraph orchestration: a bounded model<->tool loop.

One compiled graph serves both the explore and the propose phases -- the only
differences are the tool schemas, the executor, and the reasoning effort. The
loop enforces the constraints gpt-oss cannot be trusted to honour (Task.md §3):

* **exactly one tool call per step** -- a completion carrying two is a protocol
  violation and raises instead of silently dropping work;
* **a hard round cap in state** (``tool_calls_remaining``), so a loop that never
  converges stops at a known cost without relying on an exception;
* **token and wall-clock budgets checked before every model call and every tool
  execution**, so a run cannot overspend between checks.

The graph is stateless on disk. State travels entirely in :class:`GraphState`;
the approval pause and the apply/verify/commit/push phases live in the runner
and the Django database (Q9) rather than in a LangGraph checkpointer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph

from apps.agent.budget import Budget
from apps.agent.errors import ModelOutputError
from apps.agent.providers.base import Completion, Provider, ToolCall
from apps.agent.providers.normalize import normalize, tool_calls_from_ai
from apps.agent.tools import ToolResult

__all__: list[str] = [
    "DEFAULT_MAX_TOOL_CALLS",
    "DEFAULT_PER_CALL_TOKEN_CAP",
    "ExecOutcome",
    "GraphState",
    "ProposedPatch",
    "build_agent_graph",
    "completion_to_ai_message",
    "initial_state",
]

#: A completion bigger than this is a runaway, not a deliverable. Costs are
#: capped per call on top of the run budget.
DEFAULT_PER_CALL_TOKEN_CAP = 12_000
DEFAULT_MAX_TOOL_CALLS = 25


@dataclass(slots=True)
class ProposedPatch:
    """One validated, single-file change the model wants to make.

    ``error`` is empty exactly when the patch passed structural validation and
    is storable as a :class:`~apps.agent_tasks.models.ProposedChange` row. It
    says nothing yet about whether the patch *applies* -- that is decided at
    apply time by :mod:`apps.git.patch.apply_patch` with its escalation chain.
    """

    path: str
    change_type: str
    patch: str
    rationale: str = ""
    error: str = ""

    @property
    def valid(self) -> bool:
        return not self.error


@dataclass(frozen=True, slots=True)
class ExecOutcome:
    """What a tool execution returned: feedback for the model and any side
    effects the graph should record in state."""

    result: ToolResult
    proposal: ProposedPatch | None = None
    summary: str | None = None


class GraphState(TypedDict, total=False):
    """The full conversational + proposal state of one graph run."""

    messages: list[BaseMessage]
    reasoning: list[str]
    proposals: list[ProposedPatch]
    final_summary: str
    tool_calls_remaining: int
    stopped_reason: str


def build_agent_graph(
    *,
    provider: Provider,
    tool_schemas: list[dict[str, Any]],
    executor: Callable[[ToolCall], ExecOutcome],
    budget: Budget,
    reasoning_effort: str,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    per_call_token_cap: int = DEFAULT_PER_CALL_TOKEN_CAP,
) -> Any:
    """Compile the model<->tool loop.

    ``executor`` is a closure that already knows which phase it belongs to (the
    read-only registry for explore, the propose controller for propose), so the
    graph itself stays phase-agnostic.
    """
    limit = max_tool_calls

    def call_model(state: GraphState) -> dict[str, Any]:
        budget.check()
        if state.get("tool_calls_remaining", limit) <= 0:
            # Last run_tool consumed the round cap; stop before spending a
            # model call on a loop that has proven it will not converge.
            return {"stopped_reason": "tool_calls_exhausted"}

        completion = provider.complete(
            normalize(state["messages"]),
            tools=tool_schemas,
            reasoning_effort=reasoning_effort,
            max_tokens=budget.clamp_max_tokens(per_call_token_cap),
        )
        budget.record_usage(
            input_tokens=completion.usage.input_tokens,
            output_tokens=completion.usage.output_tokens,
            reasoning_tokens=completion.usage.reasoning_tokens,
            cached_tokens=completion.usage.cached_tokens,
        )

        if len(completion.tool_calls) > 1:
            raise ModelOutputError(
                f"gpt-oss cannot emit parallel tool calls (got {len(completion.tool_calls)})"
            )

        updates: dict[str, Any] = {
            "messages": [*state["messages"], completion_to_ai_message(completion)],
        }
        if completion.reasoning:
            updates["reasoning"] = [*state.get("reasoning", []), completion.reasoning]
        return updates

    def run_tool(state: GraphState) -> dict[str, Any]:
        budget.check()
        last = state["messages"][-1]
        if not isinstance(last, AIMessage):
            # The only turns the graph appends are assistant and tool turns; a
            # tool turn arriving here means the conditional edge misfired.
            raise ModelOutputError("run_tool reached without a pending assistant tool call")
        calls = tool_calls_from_ai(last)
        if len(calls) != 1:
            raise ModelOutputError(f"expected exactly one tool call, got {len(calls)}")
        budget.record_tool_call()

        outcome = executor(calls[0])
        updates: dict[str, Any] = {
            "messages": [
                *state["messages"],
                ToolMessage(content=outcome.result.content, tool_call_id=calls[0].id),
            ],
            "tool_calls_remaining": state.get("tool_calls_remaining", limit) - 1,
        }
        if outcome.proposal is not None:
            updates["proposals"] = [*state.get("proposals", []), outcome.proposal]
        if outcome.summary is not None:
            updates["final_summary"] = outcome.summary
        return updates

    def route(state: GraphState) -> str:
        if state.get("stopped_reason") or state.get("final_summary"):
            return END
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and tool_calls_from_ai(last):
            return NODE_RUN_TOOL
        return END

    def route_after_tool(state: GraphState) -> str:
        # finish() closes the propose set: end here instead of spending another
        # model call to discover what the run already knows.
        if state.get("final_summary"):
            return END
        return NODE_CALL_MODEL

    builder = StateGraph(GraphState)
    builder.add_node(NODE_CALL_MODEL, call_model)
    builder.add_node(NODE_RUN_TOOL, run_tool)
    builder.set_entry_point(NODE_CALL_MODEL)
    builder.add_conditional_edges(
        NODE_CALL_MODEL,
        route,
        {NODE_RUN_TOOL: NODE_RUN_TOOL, END: END},
    )
    builder.add_conditional_edges(
        NODE_RUN_TOOL,
        route_after_tool,
        {NODE_CALL_MODEL: NODE_CALL_MODEL, END: END},
    )
    return builder.compile()


def initial_state(
    *,
    system: SystemMessage,
    task: HumanMessage,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
) -> GraphState:
    """The starting state of every phase: the stable context plus the task."""
    return {
        "messages": [system, task],
        "reasoning": [],
        "proposals": [],
        "final_summary": "",
        "tool_calls_remaining": max_tool_calls,
        "stopped_reason": "",
    }


def completion_to_ai_message(completion: Completion) -> AIMessage:
    """Turn a parsed provider response into the assistant turn for the thread."""
    tool_calls = [
        {"id": call.id, "name": call.name, "args": call.arguments} for call in completion.tool_calls
    ]
    return AIMessage(content=completion.content, tool_calls=tool_calls)


NODE_CALL_MODEL = "call_model"
NODE_RUN_TOOL = "run_tool"

"""Tests for prompt construction: the anti-slop contract and its ordering.

The ordering assertions matter: Groq caches a prompt prefix that is >=1024
tokens, so the stable contract and repository context must render byte-for-byte
identical across calls (Task.md §3) and the mutable task text must come last.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from apps.agent.context import InstructionFile
from apps.agent.prompts import (
    ANTI_SLOP_CONTRACT,
    REASONING_EFFORT_BY_PHASE,
    PromptContext,
    build_system_prompt,
    phase_hint,
    system_message,
    user_task_message,
)


def make_context(**overrides: object) -> PromptContext:
    defaults: dict[str, object] = {
        "repository": "carol/demo-app",
        "base_branch": "main",
        "feature_branch": "codepilot/add-login-form",
        "repo_map": ".\n`-- README.md",
        "stack": {"languages": ["Python"], "managers": ["uv"]},
        "instructions": (),
        "toolchain_commands": {"test": "python -m pytest", "lint": "python -m ruff check"},
    }
    defaults.update(overrides)
    return PromptContext(**defaults)  # type: ignore[arg-type]


class TestSystemPrompt:
    def test_includes_header_contract_and_rules(self) -> None:
        text = build_system_prompt(make_context())
        assert "CodePilotAI" in text
        assert "Non-negotiables" in text
        assert "Forbidden" in text
        assert "How changes are made" in text

    def test_includes_repository_context(self) -> None:
        text = build_system_prompt(make_context())
        assert "carol/demo-app" in text
        assert "codepilot/add-login-form" in text
        assert "Python" in text
        assert "uv" in text
        assert "python -m pytest" in text

    def test_repo_map_carries_no_fence_ambiguity(self) -> None:
        ctx = make_context(repo_map=".\n`-- src\n    `-- main.py")
        text = build_system_prompt(ctx)
        assert "src" in text
        assert "main.py" in text

    def test_instructions_are_injected_verbatim(self) -> None:
        ctx = make_context(
            instructions=(
                InstructionFile(
                    path="AGENTS.md",
                    content="Never use tabs. English identifiers only.",
                ),
                InstructionFile(
                    path="CLAUDE.md", content="Prefer stdlib over deps.", truncated=False
                ),
            )
        )
        text = build_system_prompt(ctx)
        assert "Repository conventions" in text
        assert "Never use tabs" in text
        assert "Prefer stdlib over deps" in text

    def test_truncated_instruction_is_marked(self) -> None:
        ctx = make_context(
            instructions=(InstructionFile(path="AGENTS.md", content="x" * 100, truncated=True),)
        )
        assert "(truncated)" in build_system_prompt(ctx)

    def test_deterministic_rendering(self) -> None:
        first = build_system_prompt(make_context())
        second = build_system_prompt(make_context())
        assert first == second

    def test_contract_precedes_repository_context(self) -> None:
        text = build_system_prompt(make_context())
        assert text.index("Non-negotiables") < text.index("carol/demo-app")


class TestMessageBuilders:
    def test_system_message_is_a_system_message(self) -> None:
        message = system_message(make_context())
        assert isinstance(message, SystemMessage)
        assert message.content

    def test_task_message_is_a_human_message(self) -> None:
        message = user_task_message("Add a login form to the settings page.")
        assert isinstance(message, HumanMessage)
        assert message.content == "Add a login form to the settings page."


class TestPhaseBriefs:
    def test_known_phases_have_briefs(self) -> None:
        for phase in ("plan", "explore", "propose", "repair", "commit"):
            assert phase_hint(phase).strip()

    def test_unknown_phase_falls_back_to_explore(self) -> None:
        assert phase_hint("does-not-exist") == phase_hint("explore")

    def test_effort_values_are_within_gpt_oss_allowed_set(self) -> None:
        allowed = {"low", "medium", "high"}
        for phase, effort in REASONING_EFFORT_BY_PHASE.items():
            assert effort in allowed, f"phase {phase!r} uses invalid effort {effort!r}"

    def test_writing_phases_spend_more_reasoning(self) -> None:
        assert REASONING_EFFORT_BY_PHASE["propose"] == "high"
        assert REASONING_EFFORT_BY_PHASE["repair"] == "high"
        assert REASONING_EFFORT_BY_PHASE["explore"] == "low"


class TestContractContent:
    def test_anti_slop_rules_are_prohibitions_not_aspirations(self) -> None:
        for forbidden in (
            "Reformatting or restyling code outside your change",
            "Adding docstrings, comments, type hints",
            "Replacing working code",
            "Inventing a dependency",
            "Creating README",
        ):
            assert forbidden in ANTI_SLOP_CONTRACT

    def test_contract_mentions_approval_gate(self) -> None:
        assert "approves" in build_system_prompt(make_context()) or "approve" in ANTI_SLOP_CONTRACT

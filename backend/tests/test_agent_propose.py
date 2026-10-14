"""Tests for the propose and explore phases.

The propose phase is where the model's intent becomes *validated* single-file
diffs: every proposal passes the sandbox, the secret policy, structural patch
validation, and the one-file-one-patch rule before it can be persisted. Most of
this file asserts what happens *before* anything is stored.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from apps.agent.budget import Budget
from apps.agent.explore import run_explore
from apps.agent.propose import ProposeController, run_propose
from apps.agent.providers.base import ToolCall
from apps.agent.tools import ToolRegistry
from apps.git.patch import patch_files

MODIFY_PATCH = """--- a/src/app.py
+++ b/src/app.py
@@ -1,3 +1,6 @@
 def add(a, b):
     return a + b
+
+def sub(a, b):
+    return a - b
"""

CREATE_PATCH = """--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,2 @@
+def hello():
+    return "hi"
"""

DELETE_PATCH = """--- a/src/old.py
+++ /dev/null
@@ -1,3 +0,0 @@
-def gone():
-    return 1
-
"""

MULTI_FILE_PATCH = (
    MODIFY_PATCH
    + "\n"
    + """--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-# hi
+# hello
"""
)


def propose(controller: ProposeController, **args: Any) -> Any:
    return controller.run(ToolCall(id="call_1", name="propose_change", arguments=dict(args)))


def finish(controller: ProposeController, summary: str) -> Any:
    return controller.run(ToolCall(id="call_2", name="finish", arguments={"summary": summary}))


class TestProposeChangeTool:
    def test_valid_modify_is_stored(self, tmp_path: Path) -> None:
        controller = ProposeController(tmp_path)
        outcome = propose(
            controller,
            path="src/app.py",
            change_type="modify",
            patch=MODIFY_PATCH,
            rationale="Add a subtraction helper next to add.",
        )
        assert not outcome.result.is_error
        assert outcome.proposal is not None
        assert outcome.proposal.valid
        assert outcome.proposal.path == "src/app.py"
        assert controller.proposals == (outcome.proposal,)

    def test_create_with_dev_null_header(self, tmp_path: Path) -> None:
        controller = ProposeController(tmp_path)
        outcome = propose(
            controller,
            path="src/new.py",
            change_type="create",
            patch=CREATE_PATCH,
            rationale="Add a hello helper.",
        )
        assert not outcome.result.is_error
        assert patch_files(CREATE_PATCH) == ["src/new.py"]

    def test_delete_with_dev_null_body(self, tmp_path: Path) -> None:
        controller = ProposeController(tmp_path)
        outcome = propose(
            controller,
            path="src/old.py",
            change_type="delete",
            patch=DELETE_PATCH,
            rationale="The old helper is unused.",
        )
        assert not outcome.result.is_error
        assert patch_files(DELETE_PATCH) == ["src/old.py"]

    def test_multifile_patch_is_refused(self, tmp_path: Path) -> None:
        controller = ProposeController(tmp_path)
        outcome = propose(
            controller,
            path="src/app.py",
            change_type="modify",
            patch=MULTI_FILE_PATCH,
            rationale="Two files at once.",
        )
        assert outcome.result.is_error
        assert "exactly one file" in outcome.result.content
        assert controller.proposals == ()

    def test_path_must_match_the_patch(self, tmp_path: Path) -> None:
        controller = ProposeController(tmp_path)
        outcome = propose(
            controller,
            path="src/app.py",
            change_type="modify",
            patch=CREATE_PATCH,  # creates src/new.py
            rationale="Mismatch.",
        )
        assert outcome.result.is_error
        assert "must match" in outcome.result.content

    def test_secret_path_is_refused(self, tmp_path: Path) -> None:
        controller = ProposeController(tmp_path)
        (tmp_path / ".env").write_text("KEY=1\n", encoding="utf-8")
        outcome = propose(
            controller,
            path=".env",
            change_type="modify",
            patch=MODIFY_PATCH.replace("src/app.py", ".env"),
            rationale="Touching a secret file.",
        )
        assert outcome.result.is_error
        assert "secret" in outcome.result.content

    def test_traversal_is_refused(self, tmp_path: Path) -> None:
        controller = ProposeController(tmp_path)
        outcome = propose(
            controller,
            path="../outside.py",
            change_type="create",
            patch=CREATE_PATCH,
            rationale="Escape attempt.",
        )
        assert outcome.result.is_error
        assert "escapes" in outcome.result.content

    def test_generated_relative_path_is_refused(self, tmp_path: Path) -> None:
        controller = ProposeController(tmp_path)
        outcome = propose(
            controller,
            path="dist/output.js",
            change_type="create",
            patch=CREATE_PATCH,
            rationale="Write into build output.",
        )
        assert outcome.result.is_error
        assert "ignored or generated path" in outcome.result.content
        # The refusal must not silently leave the controller dirty.
        assert controller.proposals == ()

    def test_structurally_invalid_patch_is_refused_and_repairable(self, tmp_path: Path) -> None:
        controller = ProposeController(tmp_path)
        outcome = propose(
            controller,
            path="src/app.py",
            change_type="modify",
            patch="this is not a diff",
            rationale="Broken output.",
        )
        assert outcome.result.is_error
        assert "patch failed validation" in outcome.result.content
        # The model gets repair feedback with the remaining budget spelled out.
        assert "2 repair attempt(s) left" in outcome.result.content

    def test_unknown_change_type_is_refused(self, tmp_path: Path) -> None:
        controller = ProposeController(tmp_path)
        outcome = propose(
            controller,
            path="src/app.py",
            change_type="rename",
            patch=MODIFY_PATCH,
            rationale="Renames are out of scope.",
        )
        assert outcome.result.is_error
        assert "change_type" in outcome.result.content

    def test_malformed_arguments_are_tool_errors_not_crashes(self, tmp_path: Path) -> None:
        controller = ProposeController(tmp_path)
        outcome = controller.run(
            ToolCall(id="c1", name="propose_change", arguments={"path": 42, "patch": MODIFY_PATCH})
        )
        assert outcome.result.is_error
        assert "path must be a string" in outcome.result.content

    def test_unknown_tool_is_reported(self, tmp_path: Path) -> None:
        controller = ProposeController(tmp_path)
        outcome = controller.run(ToolCall(id="c1", name="rm_rf", arguments={}))
        assert outcome.result.is_error
        assert "Unknown tool" in outcome.result.content


class TestFinishTool:
    def test_empty_summary_is_refused(self, tmp_path: Path) -> None:
        outcome = finish(ProposeController(tmp_path), "")
        assert outcome.result.is_error
        assert "summary" in outcome.result.content

    def test_valid_summary_closes_the_run(self, tmp_path: Path) -> None:
        outcome = finish(ProposeController(tmp_path), "Adds a subtraction helper.")
        assert not outcome.result.is_error
        assert outcome.summary == "Adds a subtraction helper."


class TestRunPropose:
    def test_end_to_end(
        self,
        tmp_path: Path,
        scripted_provider: Any,
        tool_completion: Any,
        answer_completion: Any,
    ) -> None:
        controller = ProposeController(tmp_path)
        provider = scripted_provider(
            [
                tool_completion(
                    "propose_change",
                    {
                        "path": "src/app.py",
                        "change_type": "modify",
                        "patch": MODIFY_PATCH,
                        "rationale": "Add a subtraction helper.",
                    },
                ),
                tool_completion("finish", {"summary": "Adds subtraction."}),
            ]
        )
        budget = Budget(token_limit=1_000_000, wall_clock_seconds=120)
        result = run_propose(
            provider=provider,
            controller=controller,
            system=SystemMessage(content="sys"),
            task=HumanMessage(content="Add subtraction."),
            budget=budget,
        )

        assert result.stopped_reason == ""
        assert len(result.proposals) == 1
        assert result.proposals[0].valid
        assert result.summary == "Adds subtraction."
        # finish() ends the run: the thread closes with the finish tool call
        # and its outcome, without spending a model call on a farewell turn.
        assert isinstance(result.messages[-2], AIMessage)
        assert isinstance(result.messages[-1], ToolMessage)
        assert "Proposal set complete" in result.messages[-1].content
        # High effort is used for the writing phase, and every cost is counted.
        assert provider.kwargs[0]["reasoning_effort"] == "high"
        assert budget.llm_calls == 2
        assert budget.tool_calls == 2

    def test_repair_loop_recovers_and_only_valid_patches_survive(
        self,
        tmp_path: Path,
        scripted_provider: Any,
        tool_completion: Any,
        answer_completion: Any,
    ) -> None:
        controller = ProposeController(tmp_path)
        provider = scripted_provider(
            [
                tool_completion(
                    "propose_change",
                    {
                        "path": "src/app.py",
                        "change_type": "modify",
                        "patch": "junk",
                        "rationale": "Broken diff, will fix.",
                    },
                ),
                tool_completion(
                    "propose_change",
                    {
                        "path": "src/app.py",
                        "change_type": "modify",
                        "patch": MODIFY_PATCH,
                        "rationale": "Fixed the diff.",
                    },
                ),
                tool_completion("finish", {"summary": "Adds subtraction."}),
            ]
        )
        result = run_propose(
            provider=provider,
            controller=controller,
            system=SystemMessage(content="sys"),
            task=HumanMessage(content="Add subtraction."),
            budget=Budget(token_limit=1_000_000, wall_clock_seconds=120),
        )
        assert len(result.proposals) == 1
        assert result.proposals[0].valid
        # The failed attempt is visible in the thread as a tool error, so the
        # full story is auditable even though only the good patch survived.
        tool_messages = [m for m in result.messages if isinstance(m, ToolMessage)]
        assert any("patch failed validation" in m.content for m in tool_messages)
        assert result.summary == "Adds subtraction."

    def test_run_without_finish_returns_no_summary(
        self,
        tmp_path: Path,
        scripted_provider: Any,
        answer_completion: Any,
    ) -> None:
        controller = ProposeController(tmp_path)
        provider = scripted_provider([answer_completion("I looked and decided to stop.")])
        result = run_propose(
            provider=provider,
            controller=controller,
            system=SystemMessage(content="sys"),
            task=HumanMessage(content="Add subtraction."),
            budget=Budget(token_limit=1_000_000, wall_clock_seconds=120),
        )
        # A model that answers without proposing or finishing produced nothing
        # to approve, and the run ended normally rather than hanging the loop.
        assert result.proposals == ()
        assert result.summary == ""
        assert result.stopped_reason == ""


class TestRunExplore:
    def test_read_only_loop(
        self,
        tmp_path: Path,
        scripted_provider: Any,
        tool_completion: Any,
        answer_completion: Any,
    ) -> None:
        root = tmp_path / "repo"
        (root / "src").mkdir(parents=True)
        (root / "src" / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        registry = ToolRegistry(root)
        provider = scripted_provider(
            [
                tool_completion("read_file", {"path": "src/app.py"}),
                answer_completion("I found the add function."),
            ]
        )
        result = run_explore(
            provider=provider,
            registry=registry,
            system=SystemMessage(content="sys"),
            task=HumanMessage(content="Where is add?"),
            budget=Budget(token_limit=1_000_000, wall_clock_seconds=120),
        )
        assert result.stopped_reason == ""
        tool_messages = [m for m in result.messages if isinstance(m, ToolMessage)]
        assert len(tool_messages) == 1
        assert "def add" in tool_messages[0].content
        assert isinstance(result.messages[-1], AIMessage)

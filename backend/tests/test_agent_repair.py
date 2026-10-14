"""Tests for the repair phase: applying corrected diffs inside the approved
boundary, and only through the same gates that guard proposals.

Repair is the one post-approval write path -- the model may *apply* a repaired
diff to the worktree immediately. Everything here asserts the boundary holds:
the sandbox, the one-file rule, structural validation, and the immediate-apply
+ revert-on-partial contract, plus the bounded graph round over
``RepairController``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from apps.agent.budget import Budget
from apps.agent.providers.base import ToolCall
from apps.agent.repair import RepairController, run_repair
from apps.git import worktree
from apps.git.patch import ApplyOutcome

pytestmark = pytest.mark.django_db

# app.py in the seeded origin is exactly:
#   def main():
#       return 1
REPAIR_PATCH = """\
--- a/app.py
+++ b/app.py
@@ -1,2 +1,5 @@
 def main():
     return 1
+
+def helper():
+    return "hi"
"""

# A hunk whose context matches nothing: nothing can land.
CONFLICTING_PATCH = """\
--- a/app.py
+++ b/app.py
@@ -99,2 +99,3 @@
 def not_there():
     return 1
+    return 2
"""

MULTI_FILE_PATCH = (
    REPAIR_PATCH
    + "\n"
    + """\
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-# demo-app
+# demo-app!
"""
)


@pytest.fixture
def tree(git_workspace: Any, task: Any) -> Path:
    """A fresh worktree on the task's feature branch, branched from main."""
    return worktree.create_worktree(
        repository=task.repository,
        task_id=task.pk,
        branch=task.feature_branch,
        token="",
    )


def repair(controller: RepairController, **args: Any) -> Any:
    return controller.run(ToolCall(id="call_1", name="repair_file", arguments=dict(args)))


def finish(controller: RepairController) -> Any:
    return controller.run(ToolCall(id="call_2", name="finish", arguments={}))


class TestRepairFileTool:
    def test_valid_repair_applies_to_the_tree(self, tree: Path) -> None:
        controller = RepairController(tree)

        outcome = repair(
            controller,
            path="app.py",
            patch=REPAIR_PATCH,
            rationale="The check wants the helper to exist.",
        )

        assert not outcome.result.is_error
        assert outcome.proposal is not None
        assert outcome.proposal.valid
        assert outcome.proposal.path == "app.py"
        assert controller.applied == (outcome.proposal,)
        # The write happened immediately, inside the approved boundary.
        body = (tree / "app.py").read_text(encoding="utf-8")
        assert "def helper():" in body

    def test_path_outside_the_worktree_is_rejected(self, tree: Path) -> None:
        controller = RepairController(tree)

        outcome = repair(
            controller,
            path="../../etc/passwd",
            patch=REPAIR_PATCH,
            rationale="Escape the sandbox.",
        )

        assert outcome.result.is_error
        assert controller.applied == ()
        assert worktree.worktree_is_clean(tree)

    def test_multifile_patch_is_rejected(self, tree: Path) -> None:
        controller = RepairController(tree)

        outcome = repair(
            controller,
            path="app.py",
            patch=MULTI_FILE_PATCH,
            rationale="Two files at once.",
        )

        assert outcome.result.is_error
        assert "exactly one file" in outcome.result.content
        assert controller.applied == ()

    def test_path_and_patch_must_agree(self, tree: Path) -> None:
        controller = RepairController(tree)

        outcome = repair(
            controller,
            path="README.md",
            patch=REPAIR_PATCH,
            rationale="Wrong path on purpose.",
        )

        assert outcome.result.is_error
        assert "must match" in outcome.result.content
        assert controller.applied == ()

    def test_empty_rationale_is_rejected(self, tree: Path) -> None:
        controller = RepairController(tree)

        outcome = repair(
            controller,
            path="app.py",
            patch=REPAIR_PATCH,
            rationale="   ",
        )

        assert outcome.result.is_error
        assert "rationale" in outcome.result.content
        assert worktree.worktree_is_clean(tree)

    def test_a_conflicting_patch_is_rejected_without_touching_the_tree(self, tree: Path) -> None:
        controller = RepairController(tree)

        outcome = repair(
            controller,
            path="app.py",
            patch=CONFLICTING_PATCH,
            rationale="Context that does not exist.",
        )

        # Nothing could land, so the apply registers as partial (git wrote a
        # .rej) and the repair is unwound and rejected.
        assert outcome.result.is_error
        assert "reverted" in outcome.result.content
        assert controller.applied == ()
        assert worktree.worktree_is_clean(tree)
        assert not list(tree.glob("*.rej"))

    def test_a_partial_application_is_reverted_before_rejection(
        self, tree: Path, monkeypatch: Any
    ) -> None:
        """A failed repair must restore the file to its *pre-repair* state --
        the approved change stays, only the attempted repair is undone."""
        before = (tree / "app.py").read_bytes()

        def fake_apply(worktree: Path, patch: str) -> ApplyOutcome:
            # Simulate one hunk landing and one being rejected: the file now
            # carries the repair's partial edit.
            (worktree / "app.py").write_bytes(before + b"\n# landed hunk\n")
            return ApplyOutcome(
                applied=True,
                clean=False,
                files=("app.py",),
                three_way_files=(),
                rejected=(("app.py", ("@@ -99,2 +99,3 @@",)),),
            )

        monkeypatch.setattr("apps.agent.repair.apply_patch", fake_apply)

        controller = RepairController(tree)
        outcome = repair(
            controller,
            path="app.py",
            patch=REPAIR_PATCH,
            rationale="Partial apply on purpose.",
        )

        assert outcome.result.is_error
        assert "reverted" in outcome.result.content
        # The landed hunk is gone and no .rej scaffolding survives.
        assert (tree / "app.py").read_bytes() == before
        assert not list(tree.glob("*.rej"))
        assert controller.applied == ()

    def test_malformed_arguments_are_a_tool_error(self, tree: Path) -> None:
        controller = RepairController(tree)

        outcome = controller.run(ToolCall(id="call_1", name="repair_file", arguments={"path": 42}))

        assert outcome.result.is_error
        assert "must be a string" in outcome.result.content


class TestFinish:
    def test_finish_without_repairs_still_reports(self, tree: Path) -> None:
        controller = RepairController(tree)
        outcome = finish(controller)

        assert not outcome.result.is_error
        assert outcome.summary == "repairs_complete"

    def test_repairs_decrement_the_attempt_budget(self, tree: Path) -> None:
        controller = RepairController(tree, max_repairs=2)
        bad = repair(
            controller,
            path="app.py",
            patch=CONFLICTING_PATCH,
            rationale="Will fail.",
        )
        assert "1 repair attempt(s) left" in bad.result.content


class TestRunRepair:
    def test_graph_round_applies_and_finishes(
        self,
        tree: Path,
        scripted_provider: Any,
        tool_completion: Any,
    ) -> None:
        controller = RepairController(tree)
        provider = scripted_provider(
            [
                tool_completion(
                    "repair_file",
                    {
                        "path": "app.py",
                        "patch": REPAIR_PATCH,
                        "rationale": "Add the helper the check wants.",
                    },
                ),
                tool_completion("finish", {}),
            ]
        )
        budget = Budget(token_limit=1_000_000, wall_clock_seconds=120)

        result = run_repair(
            provider=provider,
            controller=controller,
            system=SystemMessage(content="sys"),
            task=HumanMessage(content="The check reports a missing helper."),
            budget=budget,
        )

        assert result.stopped_reason == ""
        assert len(result.applied) == 1
        assert result.applied[0].valid
        assert result.applied[0].path == "app.py"
        assert "def helper():" in (tree / "app.py").read_text(encoding="utf-8")
        # Every tool call and model call is billed to the task budget.
        assert budget.llm_calls == 2
        assert budget.tool_calls == 2

    def test_model_that_answers_without_repairing_applies_nothing(
        self,
        tree: Path,
        scripted_provider: Any,
        answer_completion: Any,
    ) -> None:
        controller = RepairController(tree)
        provider = scripted_provider(
            [answer_completion("I believe the check is a false positive.")]
        )

        result = run_repair(
            provider=provider,
            controller=controller,
            system=SystemMessage(content="sys"),
            task=HumanMessage(content="The check failed."),
            budget=Budget(token_limit=1_000_000, wall_clock_seconds=120),
        )

        assert result.applied == ()
        assert result.stopped_reason == ""
        assert worktree.worktree_is_clean(tree)

"""Tests for ApplyChanges: the approval-gated write to the task worktree.

Covers the contract in apps.agent.apply: only APPROVED rows are touched,
already-applied patches are skipped (crash-retry idempotency), conflicts are
reverted whole and reported as FAILED, and the diffstat lands on the row.

Every test runs against a real worktree branched from a real bare origin, so
the whole chain is exercised.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from django.db import transaction

from apps.agent.apply import apply_approved_changes
from apps.agent_tasks.models import AgentTask, ProposedChange
from apps.git import worktree
from apps.git.patch import PatchConflictError, apply_patch, diffstat

pytestmark = pytest.mark.django_db

# app.py in the seeded origin is exactly:
#   def main():
#       return 1
MODIFY_APP_PY = """\
--- a/app.py
+++ b/app.py
@@ -1,2 +1,5 @@
 def main():
     return 1
+
+def helper():
+    return "hi"
"""

CREATE_FILE = """\
--- /dev/null
+++ b/new_module.py
@@ -0,0 +1 @@
+def new():
+    return "new"
"""

DELETE_APP_PY = """\
--- a/app.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def main():
-    return 1
"""

# A hunk whose context matches nothing in the file, so nothing can land.
CONFLICTING_PATCH = """\
--- a/app.py
+++ b/app.py
@@ -99,2 +99,3 @@
 def not_there():
     return 1
+    return 2
"""

# One file that lands cleanly and one that cannot: a partial application.
PARTIAL_PATCH = CREATE_FILE + "\n" + CONFLICTING_PATCH


@pytest.fixture
def tree(git_workspace: Any, task: AgentTask) -> Path:
    """A fresh worktree on the task's feature branch, branched from main."""
    return worktree.create_worktree(
        repository=task.repository,
        task_id=task.pk,
        branch=task.feature_branch,
        token="",
    )


def approved_change(
    task: AgentTask, patch: str, *, path: str = "app.py", order_index: int = 0
) -> ProposedChange:
    return ProposedChange.objects.create(
        task=task,
        order_index=order_index,
        path=path,
        change_type=ProposedChange.ChangeType.MODIFY,
        patch=patch,
        rationale="Test fixture",
        status=ProposedChange.Status.APPROVED,
    )


# ── diffstat ──────────────────────────────────────────────────────────────────


class TestDiffstat:
    def test_counts_added_and_removed_lines(self) -> None:
        assert diffstat(MODIFY_APP_PY) == (3, 0)
        assert diffstat(DELETE_APP_PY) == (0, 2)

    def test_ignores_file_headers(self) -> None:
        assert diffstat("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-x\n+x\n") == (1, 1)


# ── apply_approved_changes ────────────────────────────────────────────────────


class TestApplyApprovedChanges:
    def test_applies_approved_modifications(self, tree: Path, task: AgentTask) -> None:
        change = approved_change(task, MODIFY_APP_PY)

        summary = apply_approved_changes(task, tree)

        assert summary.success
        assert [o.path for o in summary.applied] == ["app.py"]
        change.refresh_from_db()
        assert change.status == ProposedChange.Status.APPLIED
        assert (change.additions, change.deletions) == (3, 0)
        body = (tree / "app.py").read_text(encoding="utf-8")
        assert "def helper():" in body

    def test_applies_creates_and_deletes(self, tree: Path, task: AgentTask) -> None:
        create = approved_change(task, CREATE_FILE, path="new_module.py", order_index=0)
        delete = approved_change(task, DELETE_APP_PY, order_index=1)

        summary = apply_approved_changes(task, tree)

        assert summary.success
        assert sorted(o.path for o in summary.applied) == ["app.py", "new_module.py"]
        assert (tree / "new_module.py").is_file()
        assert not (tree / "app.py").exists()
        create.refresh_from_db()
        delete.refresh_from_db()
        assert create.status == ProposedChange.Status.APPLIED
        assert delete.status == ProposedChange.Status.APPLIED

    def test_only_approved_changes_are_touched(
        self, tree: Path, task: AgentTask, connected_user: Any
    ) -> None:
        approved = approved_change(task, MODIFY_APP_PY, order_index=0)
        pending = ProposedChange.objects.create(
            task=task,
            order_index=1,
            path="new_module.py",
            change_type=ProposedChange.ChangeType.CREATE,
            patch=CREATE_FILE,
            rationale="Pending on purpose",
            status=ProposedChange.Status.PENDING,
        )
        rejected = ProposedChange.objects.create(
            task=task,
            order_index=2,
            path="README.md",
            change_type=ProposedChange.ChangeType.MODIFY,
            patch="""\
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-# demo-app
+# demo
""",
            rationale="Rejected on purpose",
            status=ProposedChange.Status.REJECTED,
        )

        summary = apply_approved_changes(task, tree)

        assert summary.success
        assert len(summary.outcomes) == 1
        approved.refresh_from_db()
        pending.refresh_from_db()
        rejected.refresh_from_db()
        assert approved.status == ProposedChange.Status.APPLIED
        assert pending.status == ProposedChange.Status.PENDING
        assert rejected.status == ProposedChange.Status.REJECTED

    def test_an_already_applied_patch_is_skipped_not_failed(
        self, tree: Path, task: AgentTask
    ) -> None:
        # Crash-retry simulation: the tree was patched, then the worker died
        # before the row could be marked applied.
        apply_patch(tree, MODIFY_APP_PY)
        change = approved_change(task, MODIFY_APP_PY)

        summary = apply_approved_changes(task, tree)

        assert summary.success
        change.refresh_from_db()
        assert change.status == ProposedChange.Status.APPLIED
        assert change.error_message == ""
        body = (tree / "app.py").read_text(encoding="utf-8")
        assert body.count("def helper():") == 1  # applied exactly once

    def test_a_conflicting_patch_marks_the_change_failed(self, tree: Path, task: AgentTask) -> None:
        change = approved_change(task, CONFLICTING_PATCH)

        summary = apply_approved_changes(task, tree)

        assert not summary.success
        change.refresh_from_db()
        assert change.status == ProposedChange.Status.FAILED
        assert "could not be applied" in change.error_message
        # The tree must be untouched, not half-patched.
        assert worktree.worktree_is_clean(tree)

    def test_a_partial_application_is_reverted_and_failed(
        self, tree: Path, task: AgentTask
    ) -> None:
        change = approved_change(task, PARTIAL_PATCH)

        summary = apply_approved_changes(task, tree)

        assert not summary.success
        change.refresh_from_db()
        assert change.status == ProposedChange.Status.FAILED
        assert "rejected" in change.error_message
        # Revert must undo the landed hunk too: the file is back to HEAD and no
        # .rej scaffolding remains.
        assert worktree.worktree_is_clean(tree)
        body = (tree / "app.py").read_text(encoding="utf-8")
        assert "def helper():" not in body
        assert not list(tree.glob("*.rej"))

    def test_partial_apply_is_a_noop_without_partial_patches(self, tree: Path) -> None:
        # Guard: apply_patch with allow_partial=False refuses partial results,
        # which is the semantics ApplyChanges relies on through needs_attention.
        with pytest.raises(PatchConflictError):
            apply_patch(tree, PARTIAL_PATCH, allow_partial=False)

    def test_no_approved_changes_is_a_success(self, tree: Path, task: AgentTask) -> None:
        summary = apply_approved_changes(task, tree)

        assert summary.success
        assert summary.applied == ()
        assert worktree.worktree_is_clean(tree)


# ── transaction behaviour ─────────────────────────────────────────────────────


class TestApplyAtomicity:
    def test_rows_and_tree_move_together(self, tree: Path, task: AgentTask) -> None:
        """A crash between row-write and tree-write must be recoverable.

        When the worker dies after the tree is patched but before the
        transaction commits, the row rolls back to APPROVED while the tree keeps
        the change. The next apply must detect the already-applied patch and mark
        the row APPLIED instead of failing with a duplicate conflict.
        """
        change = approved_change(task, MODIFY_APP_PY)

        try:
            with transaction.atomic():
                apply_approved_changes(task, tree)
                raise RuntimeError("simulated crash")
        except RuntimeError:
            pass

        change.refresh_from_db()
        assert change.status == ProposedChange.Status.APPROVED
        assert "def helper():" in (tree / "app.py").read_text(encoding="utf-8")

        summary = apply_approved_changes(task, tree)
        assert summary.success
        change.refresh_from_db()
        assert change.status == ProposedChange.Status.APPLIED


# ── surface noise ─────────────────────────────────────────────────────────────


class TestApplySummary:
    def test_as_dict_lists_applied_and_failed(self, tree: Path, task: AgentTask) -> None:
        approved_change(task, MODIFY_APP_PY, order_index=0)
        ProposedChange.objects.create(
            task=task,
            order_index=1,
            path="new_module.py",
            change_type=ProposedChange.ChangeType.MODIFY,
            patch="""\
--- a/new_module.py
+++ b/new_module.py
@@ -99,2 +99,3 @@
 def not_there():
     return 1
+    return 2
""",
            rationale="Conflicts on purpose",
            status=ProposedChange.Status.APPROVED,
        )

        summary = apply_approved_changes(task, tree)
        payload = summary.as_dict()

        assert payload["applied"] == ["app.py"]
        assert payload["failed"] == ["new_module.py"]
        assert payload["total"] == 2

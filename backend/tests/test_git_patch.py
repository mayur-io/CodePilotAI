"""Tests for unified-diff application.

The agent never emits whole files, so this module is the only thing that turns
its output into working-tree changes. It is tested against a real repository
with real diffs, because ``git apply`` behaviour -- three-way merging, reject
files, whitespace handling -- is not worth reimplementing in a mock.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from apps.git import patch as patch_module
from apps.git import worktree
from apps.git.errors import GitCommandError, PatchConflictError
from apps.git.patch import (
    ApplyOutcome,
    apply_patch,
    file_sections,
    patch_files,
    revert_conflicts,
    revert_patch,
    staged_paths,
    validate_patch,
)
from apps.git.runner import run_git
from apps.repositories.models import Repository

# ── hand-written, valid patches ──────────────────────────────────────────────
# ``app.py`` is two lines and ``doomed.txt`` is two lines, so every hunk below
# states a line count that matches the file it claims to modify.

MODIFY_APP_PY = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def main():
-    return 1
+    return 2
"""

ADD_FILE = """\
diff --git a/newfile.py b/newfile.py
new file mode 100644
index 0000000..8b1a995
--- /dev/null
+++ b/newfile.py
@@ -0,0 +1,2 @@
+x = 1
+y = 2
"""

DELETE_FILE = """\
diff --git a/doomed.txt b/doomed.txt
deleted file mode 100644
index 5ba8f86..0000000
--- a/doomed.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-one
-two
"""

# A file stripped bare but left in place. Legal git, not a legitimate change.
BLANK_FILE = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +0,0 @@
-def main():
-    return 1
"""


def _rewrite(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    run_git(["add", "--all"], cwd=path.parent)
    run_git(["commit", "--quiet", "-m", "Rewrite"], cwd=path.parent)


@pytest.fixture
def repo(git_workspace: Any, repository: Repository) -> Path:
    """A worktree with one commit containing app.py and doomed.txt."""
    path = worktree.create_worktree(
        repository=repository,
        task_id=1,
        branch="codepilot/patch-target",
        token="",
    )
    (path / "doomed.txt").write_text("one\ntwo\n", encoding="utf-8")
    worktree.stage_all(path)
    worktree.commit(path, message="Fixture content")
    assert worktree.worktree_is_clean(path)
    return path


def _shift_app_py(path: Path) -> None:
    """Move every line of app.py down by one so stale context no longer lines up."""
    _rewrite(path / "app.py", "\ndef main():\n    return 1\n")


def _original_patch_for(path: Path, body: str) -> str:
    """A real, index-bearing diff for ``app.py`` changed to ``body``, then undone."""
    (path / "app.py").write_text(body, encoding="utf-8")
    diff = run_git(["diff", "--", "app.py"], cwd=path).stdout
    run_git(["checkout", "--quiet", "--", "app.py"], cwd=path)
    return diff


# ── validate_patch ───────────────────────────────────────────────────────────


def test_validate_patch_accepts_a_well_formed_diff() -> None:
    validate_patch(MODIFY_APP_PY)
    validate_patch(ADD_FILE)
    validate_patch(DELETE_FILE)


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        ("", "patch is empty"),
        ("   \n\t\n", "patch is empty"),
        ("random prose, not a diff", "not a unified diff"),
        ("diff --git a/app.py b/app.py\n", "no hunks"),
        ("--- a/app.py\n+++ b/app.py\n", "no hunks"),
    ],
)
def test_validate_patch_rejects_malformed_output(bad: str, reason: str) -> None:
    with pytest.raises(GitCommandError) as caught:
        validate_patch(bad)

    assert reason in str(caught.value)


def test_validate_patch_refuses_a_patch_that_overshoots_the_size_limit(
    monkeypatch: Any,
) -> None:
    # A runaway model loop is not a legitimate change; refusing it keeps a worker
    # from buffering megabytes of hallucinated diff.
    monkeypatch.setattr(patch_module, "MAX_PATCH_BYTES", 64)

    with pytest.raises(GitCommandError) as caught:
        validate_patch(MODIFY_APP_PY)

    assert "exceeds" in str(caught.value)


def test_validate_patch_refuses_a_patch_that_only_deletes_every_line() -> None:
    # What a model produces when it gives up and blanks the file. The damage
    # should have to be deliberate, not the output of a degenerate loop.
    with pytest.raises(GitCommandError) as caught:
        validate_patch(BLANK_FILE)

    assert "without replacing it" in str(caught.value)


def test_validate_patch_still_allows_deleting_a_whole_file() -> None:
    # Deleting a file looks identical hunk-for-hunk; only the ``+++ /dev/null``
    # header distinguishes an intentional removal from a blanked file.
    validate_patch(DELETE_FILE)


def test_validate_patch_refuses_too_many_hunks(monkeypatch: Any) -> None:
    monkeypatch.setattr(patch_module, "MAX_HUNKS", 3)

    hunk = "@@ -1,2 +1,2 @@\n def main():\n-    return 1\n+    return 2\n"
    flood = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n" + hunk * 4

    with pytest.raises(GitCommandError) as caught:
        validate_patch(flood)

    assert "hunks" in str(caught.value)


# ── file_sections / patch_files ──────────────────────────────────────────────


def test_file_sections_read_both_sides_of_the_header() -> None:
    # A deletion only names the file on the ``---`` side, and an addition only
    # on the ``+++`` side. Reading one side alone loses half the report.
    assert [section.path for section in file_sections(DELETE_FILE)] == ["doomed.txt"]
    assert [section.path for section in file_sections(ADD_FILE)] == ["newfile.py"]
    assert [section.path for section in file_sections(MODIFY_APP_PY)] == ["app.py"]


def test_file_sections_marks_a_deletion_but_not_an_emptied_file() -> None:
    deleted = file_sections(DELETE_FILE)[0]
    blanked = file_sections(BLANK_FILE)[0]

    assert deleted.deleted is True
    assert deleted.emptied is True
    # The same hunk shape, but the file survives with nothing in it.
    assert blanked.deleted is False
    assert blanked.emptied is True


def test_patch_files_lists_targets_in_order() -> None:
    assert patch_files(MODIFY_APP_PY) == ["app.py"]
    assert patch_files(ADD_FILE) == ["newfile.py"]
    assert patch_files(DELETE_FILE) == ["doomed.txt"]
    assert patch_files(f"{ADD_FILE}\n{DELETE_FILE}") == ["newfile.py", "doomed.txt"]


# ── apply_patch: the happy paths ─────────────────────────────────────────────


def test_apply_patch_modifies_a_file(repo: Path) -> None:
    outcome = apply_patch(repo, MODIFY_APP_PY)

    assert outcome.applied is True
    assert outcome.clean is True
    assert outcome.files == ("app.py",)
    assert outcome.needs_attention is False
    assert "return 2" in (repo / "app.py").read_text(encoding="utf-8")


def test_apply_patch_creates_a_file(repo: Path) -> None:
    outcome = apply_patch(repo, ADD_FILE)

    assert outcome.clean is True
    assert (repo / "newfile.py").read_text(encoding="utf-8") == "x = 1\ny = 2\n"


def test_apply_patch_deletes_a_file(repo: Path) -> None:
    outcome = apply_patch(repo, DELETE_FILE)

    assert outcome.clean is True
    assert not (repo / "doomed.txt").exists()


def test_apply_patch_stages_nothing(repo: Path) -> None:
    # Applying must leave the change visible but unstaged, so ``stage_all`` only
    # ever picks up what the user actually approved.
    apply_patch(repo, MODIFY_APP_PY)

    assert staged_paths(repo) == []
    assert worktree.worktree_status(repo)[0].modified is True


def test_apply_patch_uses_three_way_merger_when_context_has_moved(repo: Path) -> None:
    # The model read the file several turns ago; by the time it writes the diff
    # the surrounding lines have moved. A three-way merge still lands the change.
    diff = _original_patch_for(repo, "def main():\n    return 2\n")
    _shift_app_py(repo)

    outcome = apply_patch(repo, diff)

    assert outcome.applied is True
    assert outcome.clean is True
    assert outcome.three_way_files == ("app.py",)
    assert "return 2" in (repo / "app.py").read_text(encoding="utf-8")


# ── apply_patch: partial application ─────────────────────────────────────────


def test_apply_patch_reports_a_hunk_that_cannot_be_resolved(repo: Path) -> None:
    _rewrite(repo / "app.py", "totally different\ncontent here\n")

    outcome = apply_patch(repo, MODIFY_APP_PY)

    assert outcome.clean is False
    assert outcome.needs_attention is True
    assert outcome.rejected[0][0] == "app.py"
    assert outcome.rejected[0][1], "expected the rejected hunk text"
    # Nine good hunks out of ten are worth delivering, with a precise report of
    # what the user still has to finish.
    assert "app.py" in outcome.files


def test_apply_patch_can_be_made_all_or_nothing(repo: Path) -> None:
    _rewrite(repo / "app.py", "totally different\ncontent here\n")

    with pytest.raises(PatchConflictError) as caught:
        apply_patch(repo, MODIFY_APP_PY, allow_partial=False)

    assert caught.value.failed_files == ("app.py",)
    assert "app.py" in str(caught.value)


def test_patch_conflict_error_carries_detail_for_the_client(repo: Path) -> None:
    _rewrite(repo / "app.py", "totally different\ncontent here\n")

    with pytest.raises(PatchConflictError) as caught:
        apply_patch(repo, MODIFY_APP_PY, allow_partial=False)

    error = caught.value
    assert error.patch == MODIFY_APP_PY
    assert error.failed_files
    assert error.detail
    assert "cleanly" in str(error)


def test_apply_patch_rejects_a_patch_for_a_missing_file(repo: Path) -> None:
    missing = """\
diff --git a/not-here.py b/not-here.py
--- a/not-here.py
+++ b/not-here.py
@@ -1,2 +1,2 @@
 def main():
-    return 1
+    return 2
"""
    with pytest.raises(PatchConflictError):
        apply_patch(repo, missing)

    assert not (repo / "not-here.py").exists()


# ── revert ───────────────────────────────────────────────────────────────────


def test_revert_patch_restores_the_file(repo: Path) -> None:
    apply_patch(repo, MODIFY_APP_PY)
    assert "return 2" in (repo / "app.py").read_text(encoding="utf-8")

    revert_patch(repo, MODIFY_APP_PY)

    assert "return 1" in (repo / "app.py").read_text(encoding="utf-8")
    assert worktree.worktree_is_clean(repo)


def test_revert_patch_is_safe_when_nothing_applied(repo: Path) -> None:
    # Called on cancel-after-failure. It must not raise or corrupt the tree.
    revert_patch(repo, MODIFY_APP_PY)

    assert worktree.worktree_is_clean(repo)


def test_revert_conflicts_removes_reject_files(repo: Path) -> None:
    _rewrite(repo / "app.py", "totally different\ncontent here\n")

    outcome = apply_patch(repo, MODIFY_APP_PY)
    assert (repo / "app.py.rej").exists()

    revert_conflicts(repo, outcome)

    assert not (repo / "app.py.rej").exists()


# ── ApplyOutcome ─────────────────────────────────────────────────────────────


def test_apply_outcome_needs_attention_tracks_rejections() -> None:
    clean = ApplyOutcome(applied=True, clean=True, files=("a",), three_way_files=(), rejected=())
    dirty = ApplyOutcome(
        applied=True,
        clean=False,
        files=("a",),
        three_way_files=(),
        rejected=(("a", ("hunk",)),),
    )

    assert clean.needs_attention is False
    assert dirty.needs_attention is True
    assert dirty.rejected[0][1] == ("hunk",)

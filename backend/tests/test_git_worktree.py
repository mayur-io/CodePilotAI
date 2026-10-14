"""Tests for the per-task worktree lifecycle.

Every test runs against a real mirror of a real bare repository, so the whole
chain -- clone, worktree add, edit, stage, commit, push -- is exercised.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from apps.git import mirror, worktree
from apps.git.errors import GitCommandError
from apps.git.runner import git_text, run_git
from apps.repositories.models import Repository

BRANCH = "codepilot/add-a-login-form"


@pytest.fixture
def tree(git_workspace: Any, repository: Repository) -> Path:
    """A freshly created worktree on ``BRANCH``, branched from ``main``."""
    return worktree.create_worktree(
        repository=repository,
        task_id=1,
        branch=BRANCH,
        token="",
    )


def test_create_worktree_checks_out_the_base_branch(tree: Path) -> None:
    assert tree.is_dir()
    assert git_text(["rev-parse", "--abbrev-ref", "HEAD"], cwd=tree) == BRANCH
    assert (tree / "README.md").read_text(encoding="utf-8").startswith("# demo-app")


def test_create_worktree_is_idempotent(git_workspace: Any, repository: Repository) -> None:
    first = worktree.create_worktree(repository=repository, task_id=1, branch=BRANCH, token="")
    (first / "README.md").write_text("dirty\n", encoding="utf-8")

    second = worktree.create_worktree(repository=repository, task_id=1, branch=BRANCH, token="")

    # A retry after a crashed worker must find a clean tree, not a dirty one.
    assert second == first
    assert (second / "README.md").read_text(encoding="utf-8").startswith("# demo-app")
    assert worktree.worktree_is_clean(second)


def test_create_worktree_rejects_an_invalid_branch(
    git_workspace: Any, repository: Repository
) -> None:
    with pytest.raises(GitCommandError) as caught:
        worktree.create_worktree(
            repository=repository, task_id=1, branch="codepilot/../escape", token=""
        )

    assert "invalid branch name" in str(caught.value)
    assert not worktree.worktree_path_for(1).exists()


def test_create_worktree_uses_the_explicit_base_ref(
    git_workspace: Any, repository: Repository
) -> None:
    mirror_path = mirror.ensure_mirror(repository, token="")
    git_text(["branch", "release-1", "main"], cwd=mirror_path)
    expected = mirror.resolve_ref(mirror_path, "release-1")
    assert expected is not None

    path = worktree.create_worktree(
        repository=repository,
        task_id=2,
        branch="codepilot/from-release",
        base_ref="release-1",
        token="",
    )

    assert git_text(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path) == "codepilot/from-release"
    assert git_text(["rev-parse", "HEAD"], cwd=path) == expected


def test_create_worktree_on_an_empty_repository_uses_an_orphan_branch(
    git_workspace: Any, repository: Repository, tmp_path: Path, git_cli: Any
) -> None:
    # A different repository name, so this does not collide with the `origin`
    # fixture's repository at the same URL.
    repository.full_name = "carol/empty-app"
    empty = tmp_path / "origin" / "carol" / "empty-app.git"
    empty.mkdir(parents=True)
    git_cli(["init", "--bare", "--quiet", str(empty)], cwd=tmp_path)

    path = worktree.create_worktree(
        repository=repository, task_id=3, branch="codepilot/first-ever", token=""
    )

    # No commit exists anywhere, so the branch starts unborn rather than failing.
    assert worktree.has_commits(path) is False
    assert git_text(["symbolic-ref", "--short", "HEAD"], cwd=path) == "codepilot/first-ever"


def test_first_commit_in_an_orphan_worktree_works(
    git_workspace: Any, repository: Repository, tmp_path: Path, git_cli: Any
) -> None:
    repository.full_name = "carol/empty-app"
    empty = tmp_path / "origin" / "carol" / "empty-app.git"
    empty.mkdir(parents=True)
    git_cli(["init", "--bare", "--quiet", str(empty)], cwd=tmp_path)

    path = worktree.create_worktree(
        repository=repository, task_id=4, branch="codepilot/first-ever", token=""
    )
    (path / "README.md").write_text("# brand new\n", encoding="utf-8")

    assert worktree.stage_all(path) == ["README.md"]
    assert worktree.commit(path, message="Initial commit") is not None
    assert worktree.has_commits(path) is True


def test_stage_and_commit_produce_the_agent_identity(tree: Path) -> None:
    (tree / "login.py").write_text("def login():\n    pass\n", encoding="utf-8")

    staged = worktree.stage_all(tree)
    sha = worktree.commit(tree, message="Add login")

    assert staged == ["login.py"]
    assert sha is not None
    assert git_text(["log", "-1", "--format=%s"], cwd=tree) == "Add login"
    assert git_text(["log", "-1", "--format=%ae"], cwd=tree).endswith("codepilotai.invalid")


def test_commit_returns_none_when_there_is_nothing_to_commit(tree: Path) -> None:
    # The normal outcome when a user rejects every proposed change.
    assert worktree.commit(tree, message="Nothing") is None


def test_commit_can_be_forced_to_be_empty(tree: Path) -> None:
    before = git_text(["rev-parse", "HEAD"], cwd=tree)

    sha = worktree.commit(tree, message="Empty but explicit", allow_empty=True)

    assert sha is not None
    assert sha != before


def test_worktree_status_reports_added_modified_and_deleted(tree: Path) -> None:
    (tree / "app.py").write_text("def main():\n    return 2\n", encoding="utf-8")
    (tree / "added.py").write_text("x = 1\n", encoding="utf-8")
    run_git(["rm", "--quiet", "--force", "README.md"], cwd=tree)

    entries = {entry.path: entry for entry in worktree.worktree_status(tree)}

    assert entries["app.py"].modified is True
    assert entries["added.py"].untracked is True
    assert entries["README.md"].deleted is True


def test_worktree_status_reports_a_rename_with_both_paths(tree: Path) -> None:
    run_git(["mv", "app.py", "application.py"], cwd=tree)

    entry = next(item for item in worktree.worktree_status(tree) if item.path == "application.py")

    assert entry.original_path == "app.py"
    assert "R" in entry.code


def test_worktree_is_clean_tracks_the_index_and_the_tree(tree: Path) -> None:
    assert worktree.worktree_is_clean(tree) is True

    (tree / "app.py").write_text("changed\n", encoding="utf-8")
    assert worktree.worktree_is_clean(tree) is False

    run_git(["add", "--all"], cwd=tree)
    run_git(["commit", "--quiet", "-m", "change"], cwd=tree)
    assert worktree.worktree_is_clean(tree) is True


def test_uncommitted_diff_includes_untracked_files(tree: Path) -> None:
    (tree / "app.py").write_text("def main():\n    return 2\n", encoding="utf-8")
    (tree / "brand_new.py").write_text("x = 1\n", encoding="utf-8")

    diff = worktree.uncommitted_diff(tree)

    assert "+    return 2" in diff
    assert "brand_new.py" in diff
    assert "+x = 1" in diff


def test_uncommitted_diff_leaves_nothing_staged(tree: Path) -> None:
    # `uncommitted_diff` uses `git add --intent-to-add` to render untracked files
    # as additions. If that is not undone, the next `stage_all` silently picks up
    # files the user never approved.
    (tree / "brand_new.py").write_text("x = 1\n", encoding="utf-8")

    worktree.uncommitted_diff(tree)

    assert worktree.stage_all(tree) == ["brand_new.py"]
    assert worktree.worktree_is_clean(tree) is False
    # The file is still unstaged from the user's point of view until they ask.
    run_git(["reset", "--quiet"], cwd=tree)
    assert {entry.path for entry in worktree.worktree_status(tree)} == {"brand_new.py"}


def test_uncommitted_diff_skips_editor_cruft(tree: Path) -> None:
    (tree / ".DS_Store").write_text("junk\n", encoding="utf-8")
    (tree / "app.py").write_text("real change\n", encoding="utf-8")

    diff = worktree.uncommitted_diff(tree)

    assert "app.py" in diff
    assert ".DS_Store" not in diff


def test_uncommitted_diff_against_a_base_ref(tree: Path) -> None:
    (tree / "app.py").write_text("def main():\n    return 2\n", encoding="utf-8")
    worktree.stage_all(tree)
    worktree.commit(tree, message="Change main")

    # Nothing is outstanding in the working tree...
    assert worktree.uncommitted_diff(tree) == ""
    # ...but the diff that becomes the pull request is not empty.
    pr_diff = worktree.uncommitted_diff(tree, base="main")
    assert "+    return 2" in pr_diff


def test_changed_files_lists_committed_changes(tree: Path) -> None:
    (tree / "app.py").write_text("def main():\n    return 2\n", encoding="utf-8")
    (tree / "extra.py").write_text("x = 1\n", encoding="utf-8")
    worktree.stage_all(tree)
    worktree.commit(tree, message="Two files")

    assert sorted(worktree.changed_files(tree, base="main")) == ["app.py", "extra.py"]


def test_push_branch_publishes_the_commit(
    tree: Path, git_workspace: Any, origin: Path, origin_seed: Path, git_cli: Any
) -> None:
    (tree / "login.py").write_text("def login():\n    pass\n", encoding="utf-8")
    worktree.stage_all(tree)
    worktree.commit(tree, message="Add login")

    worktree.push_branch(tree, branch=BRANCH, token="")

    git_cli(["remote", "add", "origin", str(origin)], cwd=origin_seed)
    git_cli(["fetch", "--quiet", "origin"], cwd=origin_seed)
    published = git_cli(
        ["rev-parse", "--verify", f"refs/remotes/origin/{BRANCH}"], cwd=origin_seed
    ).strip()
    assert published == git_text(["rev-parse", "HEAD"], cwd=tree)


def test_push_without_force_with_lease_refuses_to_clobber(
    tree: Path, git_workspace: Any, origin: Path, origin_seed: Path, git_cli: Any
) -> None:
    git_cli(["remote", "add", "origin", str(origin)], cwd=origin_seed)

    (tree / "login.py").write_text("def login():\n    pass\n", encoding="utf-8")
    worktree.stage_all(tree)
    worktree.commit(tree, message="Agent commit")
    worktree.push_branch(tree, branch=BRANCH, token="")

    # A human pushes to the same branch after the agent's push. Nothing in the
    # mirror has fetched since, so the mirror's remote-tracking ref still holds
    # the agent's commit -- which is exactly what --force-with-lease compares
    # against.
    git_cli(["fetch", "--quiet", "origin"], cwd=origin_seed)
    git_cli(["checkout", "--quiet", "-B", BRANCH, f"origin/{BRANCH}"], cwd=origin_seed)
    (origin_seed / "human.py").write_text("human = True\n", encoding="utf-8")
    git_cli(["add", "--all"], cwd=origin_seed)
    git_cli(["commit", "--quiet", "-m", "Human commit"], cwd=origin_seed)
    git_cli(["push", "--quiet", "origin", BRANCH], cwd=origin_seed)

    # --force-with-lease must refuse rather than silently deleting the human's
    # commit. A plain --force would have destroyed it.
    with pytest.raises(GitCommandError) as caught:
        worktree.push_branch(tree, branch=BRANCH, token="", force=True)

    assert "stale info" in str(caught.value).lower() or "rejected" in str(caught.value).lower()


def test_remove_worktree_deletes_the_checkout(tree: Path, git_workspace: Any) -> None:
    mirror_path = mirror.mirror_path_for("carol/demo-app")

    worktree.remove_worktree(mirror_path, tree)

    assert not tree.exists()


def test_remove_worktree_by_task_recovers_the_mirror(tree: Path) -> None:
    assert worktree.remove_worktree_by_task(1) is True
    assert not tree.exists()
    assert worktree.remove_worktree_by_task(1) is False


def test_orphaned_worktree_ids_finds_unowned_directories(
    tree: Path, git_workspace: Any, task: Any
) -> None:
    # Task 1 exists in the database, so its directory is not an orphan.
    assert worktree.orphaned_worktree_ids() == []

    worktree.worktree_root().mkdir(parents=True, exist_ok=True)
    (worktree.worktree_root() / "9999").mkdir()

    assert worktree.orphaned_worktree_ids() == [9999]


def test_cleanup_all_worktrees_removes_numeric_directories_only(
    tree: Path, git_workspace: Any
) -> None:
    (tree / "keep.txt").write_text("not a worktree\n", encoding="utf-8")
    stray = worktree.worktree_root() / "not-a-task"
    stray.mkdir(parents=True)

    removed = worktree.cleanup_all_worktrees()

    assert removed == 1
    assert not tree.exists()
    assert stray.exists()


def test_list_local_branches_includes_the_feature_branch(tree: Path) -> None:
    branches = worktree.list_local_branches(tree)

    assert BRANCH in branches


def test_has_commits_is_false_before_the_first_commit(tree: Path) -> None:
    run_git(["checkout", "--quiet", "--orphan", "fresh"], cwd=tree)
    run_git(["rm", "--quiet", "-rf", "."], cwd=tree, check=False)

    assert worktree.has_commits(tree) is False


def test_two_worktrees_do_not_share_an_index(git_workspace: Any, repository: Repository) -> None:
    first = worktree.create_worktree(
        repository=repository, task_id=10, branch="codepilot/first", token=""
    )
    second = worktree.create_worktree(
        repository=repository, task_id=11, branch="codepilot/second", token=""
    )

    (first / "only-first.py").write_text("a = 1\n", encoding="utf-8")

    assert "only-first.py" in {entry.path for entry in worktree.worktree_status(first)}
    assert "only-first.py" not in {entry.path for entry in worktree.worktree_status(second)}
    assert second.is_dir()

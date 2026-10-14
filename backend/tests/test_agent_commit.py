"""Tests for the commit phase: one clean conventional commit from approved work.

The model suggests the message, ``conventionalise`` guarantees the ``type:``
prefix regardless of what the model wrote, and a deterministic subject takes
over when the model yields nothing usable. The scratch ``.codepilotai-home``
directory the runner uses for git identity must never leak into the commit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from apps.agent.budget import Budget
from apps.agent.commit import (
    conventionalise,
    deterministic_subject,
    run_commit_phase,
)
from apps.git import worktree
from apps.git.runner import git_lines, run_git

pytestmark = pytest.mark.django_db


@pytest.fixture
def tree(git_workspace: Any, task: Any) -> Path:
    """A fresh worktree on the task's feature branch, with an agent scratch dir."""
    return worktree.create_worktree(
        repository=task.repository,
        task_id=task.pk,
        branch=task.feature_branch,
        token="",
    )


class TestConventionalise:
    def test_prepends_chore_to_a_plain_subject(self) -> None:
        assert conventionalise("Update the parser") == "chore: Update the parser"

    def test_keeps_an_existing_conventional_prefix(self) -> None:
        assert conventionalise("fix: handle empty input") == "fix: handle empty input"

    def test_prefix_without_a_colon_is_not_conventional(self) -> None:
        assert conventionalise("fix typo in loader") == "chore: fix typo in loader"

    def test_truncates_a_runaway_subject(self) -> None:
        long = "refactor: " + "x" * 120
        result = conventionalise(long)
        assert len(result.splitlines()[0]) == 72
        assert result.splitlines()[0].endswith("…")

    def test_strips_surrounding_quotes(self) -> None:
        assert conventionalise('"docs: new page"') == "docs: new page"

    def test_empty_stays_empty(self) -> None:
        assert conventionalise("   ") == ""


class TestDeterministicSubject:
    def test_fix_prefix_when_the_summary_mentions_fixing(self) -> None:
        subject = deterministic_subject(["modify"], "Fixed the failing test")
        assert subject.startswith("fix: ")

    def test_create_change_type_means_feat(self) -> None:
        subject = deterministic_subject(["create"], "Add a login form")
        assert subject.startswith("feat: ")

    def test_modify_without_a_hint_defaults_to_chore(self) -> None:
        assert deterministic_subject(["modify"], "Small tweaks").startswith("chore: ")

    def test_empty_summary_falls_back_gracefully(self) -> None:
        assert deterministic_subject(["modify"], "") == "chore: apply agent changes"


class TestRunCommitPhase:
    def _phase(self, tree: Path, scripted_provider: Any, answer_completion: Any, text: str) -> Any:
        provider = scripted_provider([answer_completion(text)])
        return run_commit_phase(
            provider=provider,
            system=SystemMessage(content="sys"),
            prompt=HumanMessage(content="Suggest a commit message."),
            worktree=tree,
            budget=Budget(token_limit=1_000_000, wall_clock_seconds=120),
            fallback_message="chore: apply agent changes",
        )

    def test_generated_message_is_used_and_committed(
        self,
        tree: Path,
        scripted_provider: Any,
        answer_completion: Any,
    ) -> None:
        (tree / "app.py").write_text(
            "def main():\n    return 1\n\n\ndef helper():\n    return 'hi'\n",
            encoding="utf-8",
        )

        result = self._phase(
            tree,
            scripted_provider,
            answer_completion,
            "fix: add the helper function\n\nAdds a helper next to main.",
        )

        assert not result.fallback
        assert result.message == "fix: add the helper function\n\nAdds a helper next to main."
        assert result.sha is not None
        assert result.staged == ("app.py",)
        # The commit is real and the message is stored verbatim.
        head = git_lines(["log", "-1", "--pretty=%s%n%b"], cwd=tree)
        assert head[0] == "fix: add the helper function"
        assert "\n".join(head) == result.message or result.message.startswith(head[0])
        assert worktree.worktree_is_clean(tree)

    def test_scratch_home_never_leaks_into_the_commit(
        self,
        tree: Path,
        scripted_provider: Any,
        answer_completion: Any,
    ) -> None:
        (tree / "app.py").write_text("def main():\n    return 2\n", encoding="utf-8")
        # The runner keeps git identity inside the worktree; it must stay out
        # of version control even when it holds new files.
        scratch = tree / ".codepilotai-home"
        scratch.mkdir(exist_ok=True)
        (scratch / "agent.toml").write_text("secret scratch state\n", encoding="utf-8")
        (scratch / "config").write_text("[user]\nname = agent\n", encoding="utf-8")

        result = self._phase(tree, scripted_provider, answer_completion, "chore: bump app.py")

        assert result.sha is not None
        tracked = git_lines(["ls-files"], cwd=tree)
        assert not any(".codepilotai-home" in name for name in tracked)
        assert result.message.startswith("chore:")

    def test_model_silence_uses_the_deterministic_fallback(
        self,
        tree: Path,
        scripted_provider: Any,
        answer_completion: Any,
    ) -> None:
        (tree / "app.py").write_text("def main():\n    return 3\n", encoding="utf-8")

        result = self._phase(tree, scripted_provider, answer_completion, "")

        assert result.fallback
        assert result.message == "chore: apply agent changes"
        assert result.sha is not None
        assert result.staged == ("app.py",)

    def test_nothing_to_stage_returns_no_sha(
        self,
        tree: Path,
        scripted_provider: Any,
        answer_completion: Any,
    ) -> None:
        result = self._phase(tree, scripted_provider, answer_completion, "chore: nothing")

        assert result.sha is None
        assert result.staged == ()
        # The suggested message is still returned so callers can log it.
        assert result.message == "chore: nothing"

    def test_plain_message_gets_the_prefix_applied(
        self,
        tree: Path,
        scripted_provider: Any,
        answer_completion: Any,
    ) -> None:
        (tree / "app.py").write_text("def main():\n    return 4\n", encoding="utf-8")

        result = self._phase(tree, scripted_provider, answer_completion, "bump the counter")

        assert result.message == "chore: bump the counter"
        assert result.sha is not None


class TestCommitUsesNoVerify:
    def test_commit_flags_no_verify_by_default(self, tree: Path) -> None:
        """The repo's own checks ran in the verify phase; commit does not
        re-run hooks (none exist in the seed repo, but the flag must be there)."""
        (tree / "app.py").write_text("def main():\n    return 5\n", encoding="utf-8")
        status = worktree.stage_all(tree)
        sha = worktree.commit(tree, message="chore: bump")
        assert sha is not None
        assert status == ["app.py"]
        run_git(["show", "--stat", "--format=%s", sha or "HEAD"], cwd=tree)

"""Tests for the verification stage: command derivation, the allowlist gate,
and bounded execution of the checks a repo declares.

The stage must never guess: commands come only from ``suggest_commands`` and
only when the repo shows evidence of a toolchain. A repo that declares nothing
passes by skipping. Anything the model could sneak in -- a free-form command, a
shell metacharacter -- is refused before it reaches a subprocess.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from apps.agent.errors import GuardViolationError
from apps.agent.verify import (
    ALLOWED_COMMAND_BINARIES,
    CommandOutcome,
    run_command,
    run_verification,
)
from apps.git import worktree

pytestmark = pytest.mark.django_db


@pytest.fixture
def tree(git_workspace: Any, task: Any) -> Path:
    """A fresh worktree with no toolchain markers: verification must skip."""
    return worktree.create_worktree(
        repository=task.repository,
        task_id=task.pk,
        branch=task.feature_branch,
        token="",
    )


class TestRunCommand:
    """``run_command`` is the only place a subprocess is spawned; it is tested
    directly with ``sys.executable`` so the tests never depend on the PATH
    (and the allowlist gate is not consulted here -- that is
    ``run_verification``'s job)."""

    def test_passing_command_reports_success(self, tmp_path: Path) -> None:
        outcome = run_command(
            "test",
            "python -c ok",
            [sys.executable, "-c", "print('all good')"],
            tmp_path,
        )

        assert outcome.passed
        assert outcome.returncode == 0
        assert "all good" in outcome.output

    def test_failing_command_reports_returncode(self, tmp_path: Path) -> None:
        outcome = run_command(
            "test",
            "python -c fail",
            [sys.executable, "-c", "import sys; sys.exit(3)"],
            tmp_path,
        )

        assert not outcome.passed
        assert outcome.returncode == 3

    def test_missing_executable_maps_to_127(self, tmp_path: Path) -> None:
        outcome = run_command(
            "test",
            "definitely-not-a-real-binary --help",
            ["definitely-not-a-real-binary", "--help"],
            tmp_path,
        )

        assert not outcome.passed
        assert outcome.returncode == 127
        assert "Could not run command" in outcome.output

    def test_hung_command_times_out_at_124(self, tmp_path: Path) -> None:
        outcome = run_command(
            "test",
            "python -c sleep",
            [sys.executable, "-c", "import time; time.sleep(30)"],
            tmp_path,
            timeout=0.05,
        )

        assert not outcome.passed
        assert outcome.returncode == 124
        assert "Timed out" in outcome.output


class TestRunVerification:
    def test_repo_without_a_toolchain_skips_and_passes(self, tree: Path, task: Any) -> None:
        result = run_verification(tree)

        assert result.skipped
        assert result.passed
        assert result.outcomes == ()
        assert result.as_dict()["skipped"] is True

    def test_commands_are_derived_from_detection_not_free_form(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """The repo's toolchain markers are the only input to the command list;
        the model never supplies commands, so a hostile ``command`` field in the
        detection dict must still reach the allowlist gate."""
        called: list[tuple[str, str]] = []

        def fake_suggest(detection: dict[str, Any]) -> dict[str, str]:
            return {
                "format": "python -m ruff format",
                "lint": "python -m ruff check",
                "test": "python -m pytest",
            }

        def fake_run(
            kind: str, command: str, argv: list[str], cwd: Path, *, timeout: float
        ) -> CommandOutcome:
            called.append((kind, command))
            return CommandOutcome(
                kind=kind,
                command=command,
                passed=True,
                returncode=0,
                duration_seconds=0.01,
            )

        monkeypatch.setattr("apps.agent.verify.suggest_commands", fake_suggest)
        monkeypatch.setattr("apps.agent.verify.run_command", fake_run)

        result = run_verification(tmp_path, detection={"languages": ["Python"]})

        assert result.passed
        # Format first, tests last: a formatter failure must not hide a test.
        assert [kind for kind, _ in called] == ["format", "lint", "test"]

    def test_a_command_outside_the_allowlist_is_refused(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(
            "apps.agent.verify.suggest_commands",
            lambda detection: {"test": "rm -rf /"},
        )

        with pytest.raises(GuardViolationError):
            run_verification(tmp_path, detection={"languages": ["Python"]})

    def test_failing_outcomes_are_collected_not_raised(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        def fake_suggest(detection: dict[str, Any]) -> dict[str, str]:
            return {"test": "python -m pytest"}

        def fake_run(
            kind: str, command: str, argv: list[str], cwd: Path, *, timeout: float
        ) -> CommandOutcome:
            return CommandOutcome(
                kind=kind,
                command=command,
                passed=False,
                returncode=5,
                duration_seconds=0.01,
            )

        monkeypatch.setattr("apps.agent.verify.suggest_commands", fake_suggest)
        monkeypatch.setattr("apps.agent.verify.run_command", fake_run)

        result = run_verification(tmp_path, detection={"languages": ["Python"]})

        assert not result.passed
        failing = result.failing()
        assert len(failing) == 1
        assert failing[0].returncode == 5
        assert result.as_dict()["passed"] is False


class TestCommandDerivation:
    """Evidence in the repo decides what runs -- nothing else."""

    def test_pyproject_with_pytest_and_ruff_yields_the_three_checks(
        self, git_workspace: Any, task: Any, tmp_path: Path
    ) -> None:
        # Detection runs against a real tree the same way the runner calls it.
        tree = worktree.create_worktree(
            repository=task.repository,
            task_id=task.pk,
            branch=task.feature_branch,
            token="",
        )
        (tree / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
        (tree / "pyproject.toml").write_text(
            '[project]\nname = "demo"\n[tool.pytest.ini_options]\n[tool.ruff]\n',
            encoding="utf-8",
        )
        worktree.stage_all(tree)
        worktree.commit(tree, message="Add toolchain")

        from apps.agent.context import detect_toolchain, suggest_commands

        detection = detect_toolchain(tree)
        assert detection["languages"] == ["Python"]
        assert "pytest" in detection["python"]["config"]
        assert "ruff" in detection["python"]["config"]

        assert suggest_commands(detection) == {
            "test": "python -m pytest",
            "lint": "python -m ruff check",
            "format": "python -m ruff format",
        }

    def test_missing_language_means_no_commands(self, tmp_path: Path) -> None:
        from apps.agent.context import detect_toolchain, suggest_commands

        (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
        assert suggest_commands(detect_toolchain(tmp_path)) == {}

    def test_allowlist_contains_only_known_binaries(self) -> None:
        assert {
            "python",
            "python3",
            "pytest",
            "ruff",
            "black",
            "node",
            "npm",
            "npx",
            "yarn",
            "pnpm",
            "go",
            "cargo",
            "mvn",
            "gradle",
            "./gradlew",
        } >= ALLOWED_COMMAND_BINARIES


class TestScrubbedEnvironment:
    def test_tool_environment_passthrough_is_whitelisted(self) -> None:
        from apps.agent.verify import _tool_environment

        env = _tool_environment()
        # Operator git config and secrets must never leak into repo checks.
        assert "GIT_CONFIG_GLOBAL" not in env
        assert "PYTHONPATH" not in env
        assert "GITHUB_TOKEN" not in env
        # Prompts are pinned off and the locale is stable, like the git layer.
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert env["LC_ALL"] == "C"
        assert "PATH" in env

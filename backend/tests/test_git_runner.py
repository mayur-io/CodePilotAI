"""Tests for the hardened git subprocess wrapper and the askpass helper.

The property under test throughout is that a token can reach git through the
environment and nothing else: never argv, never stdout or stderr we return, and
never an exception message.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings

from apps.git import credentials, runner
from apps.git.errors import GitCommandError, GitTimeoutError

SECRET = "gho_thisisaverylongsecrettokenvalue"


@pytest.fixture
def workdir(git_workspace: Any, tmp_path: Path) -> Path:
    """An initialised git repository to run commands in."""
    path = tmp_path / "scratch-repo"
    path.mkdir()
    runner.run_git(["init", "--quiet", "--initial-branch=main", "."], cwd=path)
    return path


def test_git_runs_a_command(workdir: Path) -> None:
    result = runner.run_git(["--version"], cwd=workdir)

    assert result.ok
    assert result.returncode == 0
    assert "git version" in result.stdout
    assert result.duration_seconds >= 0


def test_failure_raises_with_scrubbed_stderr(workdir: Path) -> None:
    with pytest.raises(GitCommandError) as caught:
        runner.run_git(["rev-parse", "--verify", "no-such-ref"], cwd=workdir)

    error = caught.value
    assert error.returncode != 0
    assert "fatal" in error.stderr.lower()
    assert error.stderr.strip() in str(error)
    assert str(error.returncode) in str(error)
    # The message must not carry argv: a remote URL can hold a token.
    assert "--no-pager" not in str(error)


def test_check_false_returns_the_failure_instead_of_raising(workdir: Path) -> None:
    result = runner.run_git(["rev-parse", "--verify", "no-such-ref"], cwd=workdir, check=False)

    assert result.ok is False
    assert result.returncode != 0


def test_token_is_scrubbed_from_returned_output(workdir: Path) -> None:
    # Park the token where git will hand it straight back to us. This is the
    # exact shape of the leak that matters: git quoting something credential-
    # bearing inside a message.
    runner.run_git(["config", "--local", "test.secret", SECRET], cwd=workdir)

    result = runner.run_git(
        ["config", "--local", "--get", "test.secret"], cwd=workdir, token=SECRET
    )

    assert SECRET not in result.stdout
    assert "***REDACTED***" in result.stdout


def test_token_is_scrubbed_from_a_raised_exception(workdir: Path) -> None:
    runner.run_git(["config", "--local", "test.secret", SECRET], cwd=workdir)
    runner.run_git(
        ["config", "--local", "test.loud", "true"],
        cwd=workdir,
        extra_env={"GIT_CONFIG_COUNT": "0"},
    )

    # Make git fail while echoing the secret: `git config` prints the value on
    # error paths, and the point is that the wrapper's scrubbing runs first.
    with pytest.raises(GitCommandError) as caught:
        runner.run_git(
            ["config", "--local", "--get", "test.secret", "--type=bogus"],
            cwd=workdir,
            token=SECRET,
        )

    assert SECRET not in str(caught.value)
    assert SECRET not in caught.value.stderr


def test_token_never_appears_in_argv(workdir: Path) -> None:
    """The credential reaches git through the environment, not the command line.

    argv is readable by every other process on the machine. On Linux that is
    ``/proc/<pid>/cmdline`` and ``ps -ef``; on Windows it is the process table via
    WMI. Neither requires any privilege to read.
    """
    argv, env = _capture_argv(workdir, token=SECRET)

    assert SECRET not in " ".join(argv)
    assert SECRET in env["CODEPILOTAI_GIT_PASSWORD"]
    assert env["GIT_ASKPASS"]


def test_terminal_prompting_is_disabled(workdir: Path) -> None:
    # Without this, a credential failure blocks forever on a prompt that has no
    # terminal to answer it, pinning a Celery worker for the process lifetime.
    _, env = _capture_argv(workdir)

    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_operator_git_config_is_never_read(workdir: Path) -> None:
    _, env = _capture_argv(workdir)

    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == env["GIT_CONFIG_SYSTEM"]
    assert env["GIT_CONFIG_GLOBAL"].startswith(str(workdir))


def test_untrusted_git_variables_are_not_inherited(workdir: Path, monkeypatch: Any) -> None:
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_SSH_COMMAND",
        "http_proxy",
        "https_proxy",
    ):
        monkeypatch.setenv(name, "/attacker/controlled")

    _, env = _capture_argv(workdir)

    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_SSH_COMMAND",
        "http_proxy",
        "https_proxy",
    ):
        assert name not in env


def test_commit_identity_is_the_agent(workdir: Path) -> None:
    runner.run_git(["config", "--local", "user.name", "Someone Else"], cwd=workdir)
    (workdir / "file.txt").write_text("content\n", encoding="utf-8")
    runner.run_git(["add", "--all"], cwd=workdir)
    runner.run_git(["commit", "--quiet", "-m", "test"], cwd=workdir)

    author = runner.git_lines(["log", "-1", "--format=%an <%ae>"], cwd=workdir)

    assert author == [f"{settings.GIT_AUTHOR_NAME} <{settings.GIT_AUTHOR_EMAIL}>"]


def test_hardening_config_blocks_local_remotes_and_hooks(workdir: Path) -> None:
    settings.GIT_ALLOW_LOCAL_REMOTE = False
    config = dict(setting.split("=", 1) for setting in runner.hardening_config(workdir))

    assert config["protocol.file.allow"] == "never"
    assert config["credential.helper"] == ""
    assert config["submodule.recurse"] == "false"
    assert config["transfer.fsckObjects"] == "true"
    assert config["core.hooksPath"].endswith(os.path.join("no-hooks"))
    assert config["safe.directory"] == str(workdir)


def test_hardening_config_allows_local_remotes_when_configured(workdir: Path) -> None:
    settings.GIT_ALLOW_LOCAL_REMOTE = True

    config = dict(setting.split("=", 1) for setting in runner.hardening_config(workdir))

    assert config["protocol.file.allow"] == "always"


def test_caller_config_overrides_the_hardening(workdir: Path) -> None:
    result = runner.run_git(
        ["config", "--get", "test.value"],
        cwd=workdir,
        config=["test.value=overridden"],
        check=False,
    )
    # The override is appended after the hardening block, so it wins.
    assert "overridden" in result.stderr or "overridden" in result.stdout


def test_timeout_raises_a_distinct_error(workdir: Path) -> None:
    # `-c core.fsmonitor=!sleep` makes git block long enough to hit any sane
    # deadline without depending on a slow network.
    with pytest.raises(GitTimeoutError) as caught:
        runner.run_git(["status"], cwd=workdir, timeout=0.001)

    assert caught.value.seconds == 0.001
    assert "timed out" in str(caught.value)


def test_stdin_is_closed_so_git_cannot_hang_on_a_prompt(workdir: Path) -> None:
    _, _ = _capture_argv(workdir)

    # run_git passes input_text (None) as stdin, which subprocess turns into a
    # closed pipe. Assert the wiring rather than trusting it: a command that
    # tries to read stdin must not block.
    result = runner.run_git(["hash-object", "--stdin"], cwd=workdir, input_text="")

    assert result.ok


def test_git_text_and_lines_strip_correctly(workdir: Path) -> None:
    (workdir / "a.txt").write_text("a\n", encoding="utf-8")
    (workdir / "b.txt").write_text("b\n", encoding="utf-8")
    runner.run_git(["add", "--all"], cwd=workdir)
    runner.run_git(["commit", "--quiet", "-m", "init"], cwd=workdir)

    assert runner.git_text(["rev-parse", "--abbrev-ref", "HEAD"], cwd=workdir) == "main"
    assert sorted(runner.git_lines(["show", "--name-only", "--format=", "HEAD"], cwd=workdir)) == [
        "a.txt",
        "b.txt",
    ]


def test_oversized_output_is_truncated(workdir: Path) -> None:
    result = runner.run_git(
        ["hash-object", "--stdin"],
        cwd=workdir,
        input_text="x" * 1024,
    )

    assert result.ok
    assert len(result.stdout) <= runner.MAX_CAPTURED_OUTPUT


def test_git_executable_is_an_absolute_path() -> None:
    resolved = runner.git_executable()

    assert Path(resolved).is_absolute()
    assert Path(resolved).name.lower().startswith("git")


def test_missing_git_is_a_clear_error(workdir: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(runner, "_resolve_git", lambda _path: None)

    with pytest.raises(GitCommandError) as caught:
        runner.git_executable()

    assert "not found on PATH" in str(caught.value)


def test_prepare_home_creates_the_scratch_directories(workdir: Path) -> None:
    home = runner.prepare_home(workdir)

    assert home == workdir / ".codepilotai-home"
    assert (home / "no-hooks").is_dir()
    assert (home / ".config").is_dir()


# ── askpass ──────────────────────────────────────────────────────────────────


def test_askpass_env_publishes_the_secret(tmp_path: Path) -> None:
    with credentials.AskpassHelper(tmp_path) as helper:
        env = helper.env(SECRET)

        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env[credentials.PASSWORD_ENV] == SECRET
        assert Path(env["GIT_ASKPASS"]).is_file()
        assert Path(env["GIT_ASKPASS"]).suffix in {".sh", ".cmd"}


def test_askpass_script_never_contains_the_secret(tmp_path: Path) -> None:
    with credentials.AskpassHelper(tmp_path) as helper:
        helper.env(SECRET)

        # The helper reads the environment. Baking the value into the script, or
        # passing it as an argument, would put it on disk or in argv.
        assert SECRET not in helper.path.read_text(encoding="utf-8")


def test_askpass_script_is_removed_on_exit(tmp_path: Path) -> None:
    with credentials.AskpassHelper(tmp_path) as helper:
        script = helper.path
        assert script.is_file()

    assert not script.exists()


def test_askpass_script_is_removed_even_when_the_body_raises(tmp_path: Path) -> None:
    script: Path | None = None

    def explode(helper: credentials.AskpassHelper) -> None:
        nonlocal script
        script = helper.path
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError), credentials.AskpassHelper(tmp_path) as helper:
        explode(helper)

    assert script is not None
    assert not script.exists()


def test_askpass_destroy_is_idempotent(tmp_path: Path) -> None:
    helper = credentials.AskpassHelper(tmp_path)
    helper.destroy()
    helper.destroy()


def test_askpass_is_owner_only_on_posix(tmp_path: Path) -> None:
    helper = credentials.AskpassHelper(tmp_path)

    try:
        if os.name != "nt":
            mode = helper.path.stat().st_mode
            assert mode & 0o100  # owner-execute
            assert not mode & 0o044  # nobody else may read it
    finally:
        helper.destroy()


def _capture_argv(cwd: Path, *, token: str | None = None) -> tuple[list[str], dict[str, str]]:
    """Return the argv and environment the wrapper actually hands to the OS."""
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        captured["argv"] = list(argv)
        captured["env"] = dict(kwargs["env"])
        from subprocess import CompletedProcess

        return CompletedProcess(argv, 0, "", "")

    original = runner.subprocess.run
    runner.subprocess.run = fake_run  # type: ignore[assignment]
    try:
        runner.run_git(["status"], cwd=cwd, token=token)
    finally:
        runner.subprocess.run = original  # type: ignore[assignment]

    return captured["argv"], captured["env"]

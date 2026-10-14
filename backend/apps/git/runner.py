"""The single choke point for every ``git`` subprocess in the project.

Design constraints, all of which are load-bearing:

* **Credentials never touch argv.** The token goes in via ``GIT_ASKPASS``
  (see :mod:`apps.git.credentials`), because argv is readable by any other
  process on the machine through ``ps`` or WMI.
* **A hardened environment.** ``GIT_CONFIG_NOSYSTEM`` and a scratch ``HOME``
  stop the operator's global git config -- ``insteadOf`` rewrites, hooks,
  credential helpers -- from redirecting our pushes somewhere unexpected.
  ``core.hooksPath`` points at an empty directory so a repository's own hooks
  cannot execute code on our behalf.
* **No interactive prompts, ever.** ``GIT_TERMINAL_PROMPT=0`` plus a closed
  stdin turns a credential failure into an error instead of a Celery worker
  hanging forever on a prompt with no terminal to answer it.
* **Output is scrubbed before it is returned, logged, or put in an exception.**
  Git will echo a URL containing a token inside a fatal message.
* **A deadline on every call.** A hung network fetch must not pin a worker.

Flags are passed with ``-c key=value`` on our own command line, which takes
precedence over system, global, repository, and environment configuration -- so
none of this can be changed by a value smuggled in through repository content.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from apps.accounts.crypto import scrub_secrets
from apps.git.credentials import AskpassHelper, askpass_helper_in
from apps.git.errors import GitCommandError, GitTimeoutError
from config.observability import get_logger

logger = get_logger(__name__)

#: Long enough for a full mirror clone of a large repository.
DEFAULT_TIMEOUT = 900.0

#: Refuse to buffer more than this from a child. A runaway command printing
#: megabytes would otherwise be held entirely in memory.
MAX_CAPTURED_OUTPUT = 32 * 1024 * 1024

#: Environment variables inherited from the worker. Everything is allowlisted:
#: inheriting wholesale is how ``GIT_DIR``, ``GIT_WORK_TREE``,
#: ``GIT_CONFIG_GLOBAL``, and assorted proxy variables leak in and make git
#: operate on a repository nobody intended.
_PASSTHROUGH_ENV = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "LANG",
)

#: Name of the scratch HOME created inside a repository directory.
_HOME_DIRNAME = ".codepilotai-home"


@lru_cache(maxsize=8)
def _resolve_git(search_path: str | None) -> str | None:
    return shutil.which("git", path=search_path)


def git_executable() -> str:
    """Absolute path to the ``git`` binary.

    This is not a convenience: it is required. Git's own global options have to
    precede the subcommand, so every argv starts with ``--no-pager``. On Windows
    ``CreateProcess`` reads argv[0] as the program name, so passing
    ``["--no-pager", ..., "status"]`` asks Windows to execute a file literally
    named ``--no-pager`` and fails with a bare ``WinError 2``. Naming the binary
    explicitly makes the leading global options work on every platform and
    removes any dependence on the caller's working directory.

    Resolved through ``PATH`` and cached per ``PATH`` value, so a test or a
    deployment that moves git mid-process is still handled correctly.
    """
    search_path = os.environ.get("PATH")
    resolved = _resolve_git(search_path)
    if resolved is None:
        raise GitCommandError(
            ("git",),
            127,
            f"git executable not found on PATH={search_path!r}",
        )
    return resolved


@dataclass(frozen=True, slots=True)
class GitResult:
    """The outcome of one ``git`` invocation."""

    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def hardening_config(repo_root: Path) -> list[str]:
    """``-c key=value`` overrides applied to every command.

    Kept as a function of the repository so ``core.hooksPath`` and
    ``safe.directory`` can name a concrete path rather than a wildcard.
    """
    return [
        # Empty directory: a checked-out repo cannot run hooks against us.
        f"core.hooksPath={repo_root / _HOME_DIRNAME / 'no-hooks'}",
        # Never consult or write credential helpers that belong to the operator.
        "credential.helper=",
        "credential.interactive=never",
        # Predictable output regardless of the operator's configuration.
        "core.abbrev=12",
        "core.pager=cat",
        "advice.detachedHead=false",
        "log.showSignature=false",
        "diff.noprefix=false",
        "diff.mnemonicPrefix=false",
        # Never let a submodule fan out or prompt during a fetch.
        "submodule.recurse=false",
        "submodule.fetchJobs=0",
        # Refuse local-path remotes: a repository's config must not be able to
        # redirect our push at a path on this worker. Tests point the mirror at
        # a bare repo in tmp_path, which is why this is a setting and not a
        # constant.
        "protocol.file.allow=" + ("always" if settings.GIT_ALLOW_LOCAL_REMOTE else "never"),
        # Verify objects on transfer rather than trusting the remote.
        "transfer.fsckObjects=true",
        # Without this git refuses to touch a repository owned by another
        # account, which happens whenever the worker runs as a different user
        # than the one that created the mirror. Scoped to this exact path
        # rather than a blanket `safe.directory=*`.
        f"safe.directory={repo_root}",
    ]


def build_environment(
    *,
    repo_root: Path,
    home: Path,
    token: str | None = None,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Assemble the environment for a git subprocess."""
    env = {key: os.environ[key] for key in _PASSTHROUGH_ENV if key in os.environ}

    # Identity, so commits and PRs are attributable to the agent.
    env["GIT_AUTHOR_NAME"] = settings.GIT_AUTHOR_NAME
    env["GIT_AUTHOR_EMAIL"] = settings.GIT_AUTHOR_EMAIL
    env["GIT_COMMITTER_NAME"] = settings.GIT_AUTHOR_NAME
    env["GIT_COMMITTER_EMAIL"] = settings.GIT_AUTHOR_EMAIL

    # Isolate from the operator's configuration.
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = str(home / ".gitconfig")
    env["GIT_CONFIG_SYSTEM"] = str(home / ".gitconfig")
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")

    # Fail fast instead of prompting.
    env["GIT_TERMINAL_PROMPT"] = "0"

    # Stable, parseable messages regardless of the operator's locale.
    env["LC_ALL"] = "C"

    if token:
        # Read by the askpass helper. Never by argv.
        env["CODEPILOTAI_GIT_PASSWORD"] = token
        env["GIT_USERNAME"] = "x-access-token"

    if extra:
        env.update(extra)

    return env


def prepare_home(repo_root: Path) -> Path:
    """Create and return the scratch HOME for commands run against ``repo_root``."""
    home = repo_root / _HOME_DIRNAME
    (home / "no-hooks").mkdir(parents=True, exist_ok=True)
    (home / ".config").mkdir(parents=True, exist_ok=True)
    return home


def run_git(
    args: Iterable[str],
    *,
    cwd: Path | str,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    check: bool = True,
    config: Sequence[str] = (),
    extra_env: Mapping[str, str] | None = None,
    input_text: str | None = None,
) -> GitResult:
    """Run ``git`` in ``cwd`` and return its scrubbed output.

    ``token``, when given, is published to the child through an askpass helper,
    so it is absent from both ``args`` and any resulting error text.

    Raises :class:`GitTimeoutError` on a deadline and :class:`GitCommandError`
    on a non-zero exit unless ``check=False``. Caller-supplied ``config``
    entries are appended after the hardening overrides and therefore win, which
    is the escape hatch for the rare case that genuinely needs it.
    """
    root = Path(cwd).resolve()
    home = prepare_home(root)
    arguments = list(args)
    executable = git_executable()

    argv = ["--no-pager", "--no-optional-locks"]
    for setting in [*hardening_config(root), *config]:
        argv += ["-c", setting]
    argv += arguments

    helper: AskpassHelper | None = None
    env_extra: dict[str, str] = {}
    if token:
        helper = askpass_helper_in(home)
        env_extra = helper.env(token)

    env = build_environment(
        repo_root=root,
        home=home,
        token=token,
        extra={**env_extra, **(extra_env or {})},
    )

    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            executable=executable,
            cwd=str(root),
            env=env,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        logger.warning(
            "git_timeout",
            args=arguments,
            cwd=str(root),
            timeout_seconds=timeout,
        )
        raise GitTimeoutError(tuple(argv), timeout, cwd=str(root)) from exc
    except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        # A missing working directory, or a git binary we are not allowed to run.
        # Both are "this machine is misconfigured", not "this repository is bad",
        # and neither should ever reach a user as a git error message.
        raise GitCommandError(
            tuple(argv),
            127,
            f"could not execute git in {root}: {exc}",
        ) from exc
    finally:
        if helper is not None:
            helper.destroy()

    duration = time.monotonic() - started
    stdout = scrub_secrets(_truncate(completed.stdout or ""), token)
    stderr = scrub_secrets(_truncate(completed.stderr or ""), token)

    result = GitResult(
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=duration,
    )

    if check and not result.ok:
        logger.warning(
            "git_command_failed",
            args=arguments,
            cwd=str(root),
            returncode=result.returncode,
            stderr=result.stderr[:500],
            duration_seconds=round(duration, 3),
        )
        raise GitCommandError(tuple(argv), result.returncode, result.stderr, cwd=str(root))

    return result


def git_text(
    args: Iterable[str],
    *,
    cwd: Path | str,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Run ``git`` and return stdout with surrounding whitespace removed."""
    return run_git(args, cwd=cwd, token=token, timeout=timeout).stdout.strip()


def git_lines(
    args: Iterable[str],
    *,
    cwd: Path | str,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    keep_blank: bool = False,
) -> list[str]:
    """Run ``git`` and return stdout split into lines.

    ``-z`` is deliberately not used: git's NUL-separated output differs per
    command, and one explicit parser per command is clearer than a heuristic.
    Blank lines are dropped because git pads record output inconsistently
    across versions.
    """
    text = run_git(args, cwd=cwd, token=token, timeout=timeout).stdout
    lines = text.splitlines()
    return lines if keep_blank else [line for line in lines if line.strip()]


def _truncate(text: str) -> str:
    if len(text) <= MAX_CAPTURED_OUTPUT:
        return text
    return text[:MAX_CAPTURED_OUTPUT] + "\n... truncated ..."


__all__: list[str] = [
    "DEFAULT_TIMEOUT",
    "GitResult",
    "build_environment",
    "git_executable",
    "git_lines",
    "git_text",
    "hardening_config",
    "prepare_home",
    "run_git",
]

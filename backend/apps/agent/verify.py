"""Verify phase: run the repository's own checks and report.

Nothing is invented here. The command list comes *only* from
:func:`apps.agent.context.suggest_commands`, which reads config the repo
actually ships (``pyproject.toml``, ``package.json`` scripts, ``go.mod``,
``Cargo.toml``, ...). A repo with no recognisable toolchain gets no commands
and therefore no failures -- an unknown stack is a valid answer, and guessing a
test command would be worse than running none (Task.md §6).

Commands run as argv lists through a scrubbed environment -- never through a
shell -- and each first token must be on the executable allowlist as
defense-in-depth. Output is captured, bounded, and stored on the task so the
client can render it and the repair phase can react to it.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.agent.context import detect_toolchain, suggest_commands
from apps.agent.errors import GuardViolationError
from apps.git.runner import DEFAULT_TIMEOUT
from config.observability import get_logger

logger = get_logger(__name__)

__all__: list[str] = [
    "ALLOWED_COMMAND_BINARIES",
    "CommandOutcome",
    "VerificationResult",
    "run_command",
    "run_verification",
]

#: First tokens a verified command may start with. Every command produced by
#: ``suggest_commands`` is on this list; anything else is refused before it
#: runs, so a fingerprinting bug cannot turn into arbitrary execution.
ALLOWED_COMMAND_BINARIES = frozenset(
    {
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
    }
)

#: Order verification outcomes land in the report: tests last so a formatter
#: failure does not hide a test failure.
_COMMAND_KINDS = ("format", "lint", "test")

#: Environment inherited from the worker, same shape as the git layer but an
#: explicit copy -- verify must never depend on apps.git internals.
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

#: Combined output kept per command. Enough to diagnose, small enough to store
#: and to feed back into the repair prompt without blowing the token budget.
_MAX_OUTPUT_CHARS = 8000


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """One formatter / linter / test invocation and its result."""

    kind: str  # "format" | "lint" | "test"
    command: str  # the command as the repo declared it, for display
    passed: bool
    returncode: int
    duration_seconds: float
    output: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "command": self.command,
            "passed": self.passed,
            "returncode": self.returncode,
            "duration_seconds": round(self.duration_seconds, 3),
            "output": self.output,
        }


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Every check outcome for one task, plus what was detected."""

    detection: dict[str, Any]
    outcomes: tuple[CommandOutcome, ...]

    @property
    def passed(self) -> bool:
        """True when no detected command failed. An empty set passes."""
        return all(outcome.passed for outcome in self.outcomes)

    @property
    def skipped(self) -> bool:
        """True when the repo declared nothing to run."""
        return not self.outcomes

    def failing(self) -> tuple[CommandOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if not outcome.passed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "skipped": self.skipped,
            "outcomes": [outcome.as_dict() for outcome in self.outcomes],
        }


def run_verification(
    worktree: Path,
    *,
    detection: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> VerificationResult:
    """Detect the toolchain and run every command the repo declares.

    ``detection`` may be supplied by the caller (the runner detects once and
    reuses it), but is always derived from ``suggest_commands`` -- the command
    list itself is never caller-owned.
    """
    detection = detection if detection is not None else detect_toolchain(worktree)
    commands = suggest_commands(detection)
    outcomes: list[CommandOutcome] = []
    for kind in _COMMAND_KINDS:
        command = commands.get(kind)
        if not command:
            continue
        argv = shlex.split(command)
        if not argv or argv[0] not in ALLOWED_COMMAND_BINARIES:
            raise GuardViolationError(command, "command is not on the verification allowlist")
        outcomes.append(run_command(kind, command, argv, worktree, timeout=timeout))

    result = VerificationResult(detection=detection, outcomes=tuple(outcomes))
    logger.info(
        "verification_finished",
        root=str(worktree),
        commands=list(commands),
        passed=result.passed,
    )
    return result


def run_command(
    kind: str,
    display: str,
    argv: list[str],
    cwd: Path,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> CommandOutcome:
    """Run one verified command in ``cwd`` and capture a bounded result."""
    env = _tool_environment()
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        seconds = round(time.monotonic() - started, 3)
        logger.warning("verify_command_timeout", command=display, seconds=timeout)
        return CommandOutcome(
            kind=kind,
            command=display,
            passed=False,
            returncode=124,
            duration_seconds=seconds,
            output=f"Timed out after {timeout:g}s.",
        )
    except OSError as exc:  # missing interpreter, bad executable...
        seconds = round(time.monotonic() - started, 3)
        logger.warning("verify_command_oserror", command=display, error=str(exc))
        return CommandOutcome(
            kind=kind,
            command=display,
            passed=False,
            returncode=127,
            duration_seconds=seconds,
            output=f"Could not run command: {exc}",
        )

    seconds = round(time.monotonic() - started, 3)
    passed = completed.returncode == 0
    logger.info(
        "verify_command_finished",
        command=display,
        kind=kind,
        passed=passed,
        returncode=completed.returncode,
        seconds=seconds,
    )
    return CommandOutcome(
        kind=kind,
        command=display,
        passed=passed,
        returncode=completed.returncode,
        duration_seconds=seconds,
        output=_bounded_output(completed.stdout, completed.stderr),
    )


def _tool_environment() -> dict[str, str]:
    """A scrubbed environment for running repo checks.

    Mirrors the git layer's isolation (no operator GIT_* config, stable locale,
    no prompts) *without* creating a scratch HOME inside the worktree: the
    verification runner must never write into the repo it is checking.
    """
    env = {key: os.environ[key] for key in _PASSTHROUGH_ENV if key in os.environ}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["LC_ALL"] = "C"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _bounded_output(stdout: str, stderr: str) -> str:
    text = f"{stdout}\n{stderr}".strip()
    if len(text) > _MAX_OUTPUT_CHARS:
        text = text[:_MAX_OUTPUT_CHARS] + f"\n… (output truncated at {_MAX_OUTPUT_CHARS} chars)"
    return text

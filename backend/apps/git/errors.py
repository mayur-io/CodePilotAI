"""Failures raised by the git layer.

Distinct types because callers genuinely act differently on each:

* :class:`GitCommandError` -- the command failed; ``stderr`` explains why.
* :class:`GitTimeoutError` -- the command hung; usually a network stall.
* :class:`PatchConflictError` -- a diff did not apply cleanly. Carries the
  per-file detail the mobile diff viewer needs, so the user can approve, reject,
  or hand-edit rather than being told only that "it failed".
* :class:`MirrorError` -- the workspace could not be prepared at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class GitError(Exception):
    """Base class for git plumbing failures."""


class GitCommandError(GitError):
    """A ``git`` subprocess exited non-zero.

    ``stderr`` is scrubbed of credentials by the caller before it lands here.
    """

    def __init__(
        self,
        command: tuple[str, ...],
        returncode: int,
        stderr: str,
        *,
        cwd: str | None = None,
    ) -> None:
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        self.cwd = cwd
        # Never include the raw argv in the message: it may carry auth material.
        super().__init__(f"git exited with status {returncode}: {stderr.strip()[:2000]}")


class GitTimeoutError(GitError):
    """A ``git`` subprocess exceeded its deadline and was killed."""

    def __init__(self, command: tuple[str, ...], seconds: float, cwd: str | None = None) -> None:
        self.command = command
        self.seconds = seconds
        self.cwd = cwd
        super().__init__(f"git timed out after {seconds:g}s")


class PatchConflictError(GitError):
    """A unified diff could not be applied cleanly.

    ``failed_files`` and ``rejected_hunks`` are surfaced verbatim to the client
    so the user can see exactly which lines did not line up.
    """

    def __init__(
        self,
        patch: str,
        *,
        failed_files: tuple[str, ...] = (),
        rejected_hunks: tuple[str, ...] = (),
        detail: str = "",
    ) -> None:
        self.patch = patch
        self.failed_files = failed_files
        self.rejected_hunks = rejected_hunks
        self.detail = detail
        summary = ", ".join(failed_files) or "unknown file"
        super().__init__(f"patch did not apply cleanly to {summary}: {detail.strip()[:1000]}")


@dataclass(frozen=True, slots=True)
class FileConflict:
    """One file's worth of conflict detail from a rejected patch."""

    path: str
    reason: str
    hunks: tuple[str, ...] = field(default_factory=tuple)


class MirrorError(GitError):
    """The local mirror for a repository is missing or unusable."""


__all__: list[str] = [
    "FileConflict",
    "GitCommandError",
    "GitError",
    "GitTimeoutError",
    "MirrorError",
    "PatchConflictError",
]

"""Applying model-authored unified diffs.

The agent never emits whole files. It emits unified diffs, and this module is
the only thing that turns them into working-tree changes. That constraint is
what makes the agent safe: a hallucinated rewrite cannot clobber a file the model
never saw, because a diff names the exact bytes it intends to change.

Applying in three escalating stages, stopping at the first that succeeds:

1. ``git apply --check`` -- verify without touching the tree.
2. ``git apply --3way`` -- fall back to a three-way merge when the surrounding
   context has moved, which is the common case when the model read the file
   several turns ago.
3. ``git apply --reject`` -- apply whatever hunks still fit and collect
   ``.rej`` files describing the rest, so the user sees *which* lines failed
   instead of losing the whole change.

Stage 3 is a deliberate partial success. Discarding a 40-line change because one
hunk out of ten missed is worse for the user than applying nine and telling them
exactly what to finish by hand.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from apps.git.errors import GitCommandError, PatchConflictError
from apps.git.runner import git_lines, run_git
from config.observability import get_logger

logger = get_logger(__name__)

#: Refuse patches larger than this. A legitimate task change is a few hundred
#: lines; a runaway model loop is not, and buffering it wastes a worker.
MAX_PATCH_BYTES = 4 * 1024 * 1024

#: Refuse more hunks than this in one patch for the same reason.
MAX_HUNKS = 400

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_NO_NEW_LINES = re.compile(r"^\+\+\+ /dev/null\b")


@dataclass(frozen=True, slots=True)
class ApplyOutcome:
    """What happened when a patch was applied."""

    applied: bool
    #: ``True`` only when every hunk landed, i.e. nothing needs user attention.
    clean: bool
    #: Files the patch touched.
    files: tuple[str, ...]
    #: Hunk counts that required three-way merging.
    three_way_files: tuple[str, ...]
    #: Files that needed ``--reject``, with the hunk text that did not fit.
    rejected: tuple[tuple[str, tuple[str, ...]], ...]

    @property
    def needs_attention(self) -> bool:
        return bool(self.rejected)


@dataclass(slots=True)
class FileSection:
    """One ``---``/``+++`` pair, and what the diff does to that file.

    Derived from the patch text alone, so it can be evaluated *before* git runs.
    ``new_counts`` holds each hunk's line count on the ``+++`` side; a hunk that
    contributes zero lines is the only way a unified diff can show a file being
    emptied while leaving it present.
    """

    path: str
    deleted: bool
    new_counts: list[int]

    @property
    def emptied(self) -> bool:
        """Every hunk leaves the new side empty, i.e. the file is stripped bare."""
        return bool(self.new_counts) and all(count == 0 for count in self.new_counts)


def _strip_prefix(raw: str) -> str:
    """Normalise one side of a ``---``/``+++`` line to a bare path."""
    target = raw.split("\t", 1)[0].split(" ", 1)[0].strip()
    if target.startswith(("a/", "b/")):
        target = target[2:]
    return target


def file_sections(patch: str) -> list[FileSection]:
    """Parse the per-file header lines of a unified diff.

    A delete writes ``+++ /dev/null`` and an add writes ``--- /dev/null``, so
    reading only one side loses a file: a deletion would disappear entirely,
    and the report shown to the user would omit a file the change removed.
    """
    parsed: list[FileSection] = []
    current: FileSection | None = None

    for line in patch.splitlines():
        if line.startswith("--- "):
            current = FileSection(path=_strip_prefix(line[4:]), deleted=False, new_counts=[])
            parsed.append(current)
        elif line.startswith("+++ ") and current is not None:
            if _NO_NEW_LINES.match(line):
                current.deleted = True
            else:
                current.path = _strip_prefix(line[4:])
        elif line.startswith("@@") and current is not None:
            header = _HUNK_HEADER.match(line)
            if header is not None:
                # An omitted count means one line, per the unified-diff format.
                current.new_counts.append(1 if header.group(4) is None else int(header.group(4)))

    return [section for section in parsed if section.path]


def validate_patch(patch: str) -> None:
    """Reject a patch before it reaches git.

    Raises :class:`GitCommandError` for structural problems; the caller treats
    that as "ask the model to fix its output" rather than as a system failure.
    """
    if not patch or not patch.strip():
        raise GitCommandError(("apply",), 128, "patch is empty")

    if len(patch.encode("utf-8", errors="replace")) > MAX_PATCH_BYTES:
        raise GitCommandError(("apply",), 128, f"patch exceeds {MAX_PATCH_BYTES} bytes")

    if "diff --git" not in patch and not patch.lstrip().startswith("---"):
        raise GitCommandError(
            ("apply",), 128, "patch is not a unified diff (no 'diff --git' or '---' header)"
        )

    hunk_count = sum(1 for line in patch.splitlines() if _HUNK_HEADER.match(line))
    if hunk_count == 0:
        raise GitCommandError(("apply",), 128, "patch contains no hunks")
    if hunk_count > MAX_HUNKS:
        raise GitCommandError(
            ("apply",), 128, f"patch contains {hunk_count} hunks, limit is {MAX_HUNKS}"
        )

    if not file_sections(patch):
        raise GitCommandError(("apply",), 128, "patch declares no files")

    # A patch that strips a file to nothing is legal git, but it is what a model
    # produces when it gives up and blanks the file. Refuse it so the damage has
    # to be deliberate rather than the output of a degenerate loop.
    emptied = [
        section.path for section in file_sections(patch) if section.emptied and not section.deleted
    ]
    if emptied:
        raise GitCommandError(
            ("apply",),
            128,
            f"patch empties a file without replacing it: {', '.join(emptied)}",
        )


def patch_files(patch: str) -> list[str]:
    """Paths a patch touches, in the order they appear."""
    files: list[str] = []
    for section in file_sections(patch):
        if section.path not in files:
            files.append(section.path)
    return files


def diffstat(patch: str) -> tuple[int, int]:
    """Count the added and removed lines in a unified diff as ``(additions, deletions)``.

    ``---``/``+++`` file headers are not content; every other line that starts
    with ``+`` or ``-`` represents one changed line. The counts drive the
    :class:`~apps.agent_tasks.models.ProposedChange` row and the PR body --
    presentation, never git itself.
    """
    additions = 0
    deletions = 0
    for raw in patch.splitlines():
        if raw.startswith(("+++", "---")):
            continue
        if raw.startswith("+"):
            additions += 1
        elif raw.startswith("-"):
            deletions += 1
    return additions, deletions


def apply_patch(worktree: Path, patch: str, *, allow_partial: bool = True) -> ApplyOutcome:
    """Apply ``patch`` inside ``worktree``.

    Raises :class:`PatchConflictError` when nothing at all could be applied, or
    when a partial application was produced but ``allow_partial`` is ``False``.
    """
    validate_patch(patch)
    targets = patch_files(patch)

    check = run_git(
        ["apply", "--check", "--whitespace=nowarn", "-"],
        cwd=worktree,
        input_text=patch,
        check=False,
    )
    if check.ok:
        apply_result = run_git(
            ["apply", "--whitespace=nowarn", "-"], cwd=worktree, input_text=patch, check=False
        )
        if apply_result.ok:
            return ApplyOutcome(
                applied=True,
                clean=True,
                files=tuple(targets),
                three_way_files=(),
                rejected=(),
            )
        check = apply_result  # fall through to the escalating strategies

    three_way = run_git(
        ["apply", "--3way", "--whitespace=nowarn", "-"],
        cwd=worktree,
        input_text=patch,
        check=False,
    )
    if three_way.ok and not _has_conflict_markers(worktree, targets):
        return ApplyOutcome(
            applied=True,
            clean=True,
            files=tuple(targets),
            three_way_files=tuple(targets),
            rejected=(),
        )

    if not allow_partial:
        raise PatchConflictError(
            patch,
            failed_files=tuple(targets),
            rejected_hunks=(),
            detail=three_way.stderr or check.stderr,
        )

    rejected = run_git(
        ["apply", "--reject", "--whitespace=nowarn", "-"],
        cwd=worktree,
        input_text=patch,
        check=False,
    )
    if not rejected.ok and not _rejects_exist(worktree, targets):
        raise PatchConflictError(
            patch,
            failed_files=tuple(targets),
            rejected_hunks=(),
            detail=rejected.stderr or three_way.stderr or check.stderr,
        )

    conflicts = _collect_rejects(worktree, targets)
    landed = tuple(path for path in targets if not conflicts.get(path))
    logger.info(
        "patch_applied_partially",
        files=list(targets),
        rejected=sorted(conflicts),
    )
    return ApplyOutcome(
        applied=bool(landed),
        clean=not conflicts,
        files=tuple(targets),
        three_way_files=(),
        rejected=tuple((path, hunks) for path, hunks in sorted(conflicts.items())),
    )


def revert_patch(worktree: Path, patch: str) -> None:
    """Best-effort reversal, used when a task is cancelled mid-apply."""
    result = run_git(
        ["apply", "--reverse", "--whitespace=nowarn", "-"],
        cwd=worktree,
        input_text=patch,
        check=False,
    )
    if not result.ok:
        logger.warning("patch_revert_failed", stderr=result.stderr[:300])


def _has_conflict_markers(worktree: Path, targets: list[str]) -> bool:
    """Detect a three-way merge that succeeded but left conflict markers.

    ``git apply --3way`` can exit 0 while writing markers, so exit status alone
    is not enough to call the result clean.
    """
    for target in targets:
        path = worktree / target
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for _ in range(2000):
                    line = handle.readline()
                    if not line:
                        break
                    if line.startswith(("<<<<<<< ", ">>>>>>> ")) or line.rstrip("\n") == "=======":
                        return True
        except OSError:
            continue
    return False


def _rejects_exist(worktree: Path, targets: list[str]) -> bool:
    return any((worktree / f"{target}.rej").is_file() for target in targets)


def _collect_rejects(worktree: Path, targets: list[str]) -> dict[str, tuple[str, ...]]:
    """Read every ``.rej`` git wrote and group the hunk text by file."""
    conflicts: dict[str, tuple[str, ...]] = {}
    for target in targets:
        reject_path = worktree / f"{target}.rej"
        if not reject_path.is_file():
            continue
        try:
            body = reject_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            conflicts[target] = ("<unreadable .rej file>",)
            continue
        hunks = tuple(chunk for chunk in body.split("\n@@ ") if chunk.strip())
        conflicts[target] = hunks or ("<no hunks recovered>",)
    return conflicts


def revert_files(worktree: Path, files: Sequence[str]) -> None:
    """Restore ``files`` to HEAD after a failed (partial) application.

    ``git apply --reverse`` is all-or-nothing and refuses a patch that only
    partially applied, so a partial application has to be undone file by file:
    tracked paths are checked out from the index (which restores both modified
    and deleted files), and files the patch created -- they now exist on disk
    but were never tracked -- are removed.
    """
    for name in files:
        if git_lines(["ls-files", "--", name], cwd=worktree):
            run_git(["checkout", "--quiet", "--", name], cwd=worktree)
        else:
            full = worktree / name
            if full.is_file():
                full.unlink()
            full.with_suffix(full.suffix + ".rej").unlink(missing_ok=True)


def revert_conflicts(worktree: Path, outcome: ApplyOutcome) -> None:
    """Delete the ``.rej`` files an apply produced."""
    for target, _hunks in outcome.rejected:
        (worktree / f"{target}.rej").unlink(missing_ok=True)


def diff_for_display(path: Path, base: str | None = None) -> list[str]:
    """Return the diff lines for a client-side viewer, splitting on newlines."""
    if base:
        text = run_git(["diff", f"{base}...HEAD"], cwd=path).stdout
    else:
        text = run_git(["diff", "HEAD"], cwd=path).stdout
    return text.splitlines()


def staged_paths(path: Path) -> list[str]:
    """Paths with staged changes."""
    return git_lines(["diff", "--cached", "--name-only"], cwd=path)


__all__: list[str] = [
    "MAX_HUNKS",
    "MAX_PATCH_BYTES",
    "ApplyOutcome",
    "FileSection",
    "apply_patch",
    "diff_for_display",
    "diffstat",
    "file_sections",
    "patch_files",
    "revert_conflicts",
    "revert_patch",
    "staged_paths",
    "validate_patch",
]

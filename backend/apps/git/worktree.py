"""Per-task worktrees: one isolated checkout per agent run.

A worktree off a bare mirror gives each task its own index and working tree
while sharing one object database, so creating one is a few milliseconds rather
than another network clone. Because the mirror's refs are the only shared
state, a task that runs ``git reset --hard`` or rewrites history cannot damage
another task's work.

Branch naming is ``codepilot/<slug>``. The prefix makes agent branches obvious
in the GitHub UI and keeps them out of a user's own branch-matching rules, and
the slug is derived from the task prompt so a user can tell two runs apart
without opening either.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from apps.git import mirror as mirror_manager
from apps.git.errors import GitCommandError
from apps.git.runner import git_lines, git_text, run_git
from apps.repositories.models import Repository
from config.observability import get_logger

logger = get_logger(__name__)

#: Every branch the agent creates carries this prefix.
BRANCH_PREFIX = "codepilot"

#: GitHub's own ref-name rules, which are what actually matter: no ``..``, no
#: ``~``/``^``, no ASCII control characters, and the whole thing must stay under
#: 255 bytes.
_INVALID_REF_CHARS = re.compile(r"[\x00-\x1f\x7f ~^:?*\[\\]")

#: Everything outside this set collapses to a dash. An allowlist rather than a
#: denylist of git-forbidden characters: ``!``, ``'``, ``#`` and friends are all
#: legal in a ref but read badly in a GitHub URL and confuse CI branch filters,
#: so a branch name is held to the CI-friendly subset.
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")

MAX_SLUG_LENGTH = 48
MAX_BRANCH_LENGTH = 100


@dataclass(frozen=True, slots=True)
class StatusEntry:
    """One record from ``git status --porcelain``.

    ``code`` is git's two-character XY status. ``staged`` and ``untracked`` are
    precomputed because every caller asks the same two questions, and getting
    them subtly different is an easy way to lose a user's file.
    """

    code: str
    path: str
    original_path: str = ""
    staged: bool = False
    untracked: bool = False

    @property
    def deleted(self) -> bool:
        return "D" in self.code

    @property
    def modified(self) -> bool:
        return "M" in self.code or "A" in self.code


def worktree_root() -> Path:
    """Root directory holding every task worktree."""
    return Path(settings.WORKSPACE_ROOT) / "worktrees"


def worktree_path_for(task_id: int) -> Path:
    """Path of the worktree belonging to ``task_id``."""
    return worktree_root() / str(task_id)


def slugify(text: str, *, fallback: str = "task") -> str:
    """Turn arbitrary task text into a branch-name-safe slug.

    The result contains only ``[a-z0-9-]``, so it can never contain a path
    separator, a ref-forbidden character, or a leading dash.
    """
    slug = _SLUG_STRIP.sub("-", text.strip().lower()).strip("-")
    slug = slug[:MAX_SLUG_LENGTH].strip("-")
    return slug or fallback


def branch_name_for(prompt: str, *, discriminator: str = "") -> str:
    """Build the feature branch name for a task prompt.

    ``discriminator`` is appended when two prompts would otherwise produce the
    same branch, which is common when a user retries a task.
    """
    slug = slugify(prompt)
    if discriminator:
        suffix = slugify(discriminator, fallback="")
        if suffix:
            slug = f"{slug}-{suffix}"[:MAX_SLUG_LENGTH].strip("-")
    branch = f"{BRANCH_PREFIX}/{slug}"
    return branch[:MAX_BRANCH_LENGTH].strip("/-") or f"{BRANCH_PREFIX}/task"


def is_valid_branch(name: str) -> bool:
    """Reject anything git or GitHub would refuse later."""
    if not name or name != name.strip():
        return False
    if len(name.encode()) > MAX_BRANCH_LENGTH:
        return False
    if name.startswith("/") or name.endswith("/") or "//" in name:
        return False
    if ".." in name or "@{" in name:
        return False
    if _INVALID_REF_CHARS.search(name):
        return False
    if any(part in {".", ".."} for part in name.split("/")):
        return False
    return not name.endswith((".lock", "."))


def resolve_base(repo: Path, base: str) -> str | None:
    """Resolve a caller-supplied base branch name to a ref that is current.

    Returns the ref to branch or diff against, or ``None`` when the repository
    has no such branch at all.

    The remote-tracking ref wins. A bare clone leaves a copy of every remote head
    in ``refs/heads/*`` that no fetch ever updates again, so resolving the bare
    name would silently build the next task on a stale commit -- the user's
    ``main`` could have moved a hundred commits since the mirror was created.
    """
    for candidate in (mirror_manager.remote_branch_ref(base), base):
        if mirror_manager.resolve_ref(repo, candidate) is not None:
            return candidate
    return None


def create_worktree(
    *,
    repository: Repository,
    task_id: int,
    branch: str,
    base_ref: str | None = None,
    token: str,
) -> Path:
    """Create (or reset) the worktree for a task and check out ``branch``.

    Idempotent: re-running with the same branch resets the working tree to the
    branch head instead of failing, which is what a retry after a crashed worker
    needs.
    """
    if not is_valid_branch(branch):
        raise GitCommandError(("worktree", branch), 128, f"invalid branch name: {branch!r}")

    mirror_path = mirror_manager.ensure_mirror(repository, token=token)
    base: str | None = base_ref or repository.default_branch

    if base is not None:
        base = resolve_base(mirror_path, base)
        if base is None:
            # An empty repository has no default branch and therefore no commit
            # to branch from. The first commit has to be made in an empty tree.
            logger.info(
                "worktree_base_missing",
                task_id=task_id,
                base_ref=base_ref or repository.default_branch,
            )

    path = worktree_path_for(task_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        remove_worktree(mirror_path, path)

    if base is None:
        # Orphan branch on an empty repository: git cannot create a worktree
        # against a ref that does not exist yet.
        path.mkdir(parents=True)
        run_git(["init", "--quiet", "--initial-branch", branch, "."], cwd=path)
        logger.info("worktree_created_orphan", task_id=task_id, branch=branch)
        return path

    if mirror_manager.resolve_ref(mirror_path, branch) is not None:
        # A previous attempt at this task left the branch behind. `worktree
        # remove` deletes the checkout but not its branch, so `worktree add -b`
        # would fail with "a branch named ... already exists" -- which is exactly
        # the retry-after-a-crashed-worker case this function has to survive.
        # The branch is task-scoped under the `codepilot/` prefix and its
        # worktree was just removed, so nothing can still be using it.
        run_git(["branch", "--quiet", "--delete", "--force", branch], cwd=mirror_path)
        logger.info("worktree_replaced_stale_branch", task_id=task_id, branch=branch)

    run_git(
        [
            "worktree",
            "add",
            "--quiet",
            "-b",
            branch,
            str(path),
            base,
        ],
        cwd=mirror_path,
        token=token,
    )
    logger.info("worktree_created", task_id=task_id, branch=branch, base=base)
    return path


def remove_worktree(mirror_path: Path, path: Path) -> None:
    """Delete a task worktree and prune its administrative entry."""
    run_git(["worktree", "remove", "--force", str(path)], cwd=mirror_path, check=False)
    run_git(["worktree", "prune"], cwd=mirror_path, check=False)
    if path.exists():
        # `worktree remove` can refuse if a file is locked on Windows.
        shutil.rmtree(path, ignore_errors=True)


def remove_worktree_by_task(task_id: int) -> bool:
    """Remove a task's worktree using its mirror. Returns whether it existed."""
    path = worktree_path_for(task_id)
    if not path.exists():
        return False
    mirror_path = _guess_mirror(path)
    if mirror_path is not None:
        remove_worktree(mirror_path, path)
        return True
    shutil.rmtree(path, ignore_errors=True)
    return True


def _guess_mirror(path: Path) -> Path | None:
    """Recover a worktree's mirror from its ``.git`` pointer file.

    A worktree's ``.git`` is a file containing ``gitdir: <mirror>/worktrees/<n>``.
    Reading it back is the only way to find the mirror after the
    :class:`~apps.agent_tasks.models.AgentTask` row is gone.
    """
    pointer = path / ".git"
    try:
        text = pointer.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    target = Path(text.removeprefix("gitdir:").strip())
    # <mirror>/worktrees/<name>/ -> <mirror>
    if target.parent.name == "worktrees":
        return target.parent.parent
    return None


def worktree_is_clean(path: Path) -> bool:
    """True when nothing is staged or modified in the worktree."""
    result = run_git(["status", "--porcelain"], cwd=path, check=False)
    return result.ok and not result.stdout.strip()


def worktree_status(path: Path) -> list[StatusEntry]:
    """Parse ``git status --porcelain=v1 -z`` into structured entries."""
    raw = run_git(["status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=path)
    entries: list[StatusEntry] = []
    # -z records are NUL-separated and rename entries carry two paths, so the
    # stream has to be walked rather than split on newlines.
    fields = [field for field in raw.stdout.split("\0") if field != ""]
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if len(record) < 4:
            continue
        code, path_name = record[:2], record[3:]
        source = ""
        if "R" in code or "C" in code:
            # A rename record is two NUL-separated fields: the destination path
            # first, then the path it was renamed from. Getting this backwards
            # silently reports the pre-rename name as the current file.
            source = fields[index] if index < len(fields) else path_name
            index += 1
        entries.append(
            StatusEntry(
                code=code,
                path=path_name,
                original_path=source,
                staged=code[0] not in {" ", "?"},
                untracked=code == "??",
            )
        )
    return entries


def changed_files(path: Path, *, base: str) -> list[str]:
    """List paths that differ between ``base`` and the worktree's HEAD."""
    resolved = _require_base(path, base)
    lines = git_lines(
        ["diff", "--name-only", f"{resolved}...HEAD"],
        cwd=path,
    )
    return [line for line in lines if line]


def stage_all(path: Path) -> list[str]:
    """Stage every change the agent made and return the staged paths.

    Only paths that pass :func:`_should_track` are staged, so the scratch
    ``.codepilotai-home`` directory the runner uses for git identity never
    leaks into a commit.
    """
    entries = worktree_status(path)
    targets = [entry.path for entry in entries if _should_track(entry.path)]
    if not targets:
        return []
    run_git(["add", "--all", "--", *targets], cwd=path)
    return git_lines(["diff", "--cached", "--name-only"], cwd=path)


def commit(
    path: Path,
    *,
    message: str,
    allow_empty: bool = False,
) -> str | None:
    """Commit the staged tree. Returns the new commit SHA, or ``None``.

    ``None`` means there was nothing to commit, which is a normal outcome: the
    user may have rejected every proposed change.
    """
    args = ["commit", "--quiet", "--no-verify", "-m", message]
    if allow_empty:
        args.insert(1, "--allow-empty")

    result = run_git(args, cwd=path, check=False)
    if not result.ok:
        combined = f"{result.stdout}\n{result.stderr}"
        if "nothing to commit" in combined or "no changes added" in combined:
            logger.info("commit_skipped_empty", cwd=str(path))
            return None
        raise GitCommandError(("commit",), result.returncode, result.stderr, cwd=str(path))

    return git_text(["rev-parse", "HEAD"], cwd=path)


def push_branch(
    path: Path,
    *,
    branch: str,
    token: str,
    force: bool = False,
) -> str:
    """Push ``branch`` to origin and return the resulting git output.

    ``--force-with-lease`` rather than ``--force``: it refuses to clobber a
    commit that appeared on the remote after the mirror last saw it, so a
    concurrent human push is never silently overwritten.
    """
    args = ["push", "--quiet", "--set-upstream"]
    if force:
        args.append("--force-with-lease")
    args += ["origin", f"{branch}:{branch}"]

    return run_git(args, cwd=path, token=token).stdout.strip()


def uncommitted_diff(path: Path, *, base: str | None = None) -> str:
    """Return the working-tree diff, including untracked files as new additions.

    ``base`` diffs against a ref instead, which is what the PR body needs.
    """
    if base:
        # Strip only the trailing newline git appends; interior blank lines are
        # load-bearing context in a diff.
        resolved = _require_base(path, base)
        return run_git(["diff", f"{resolved}...HEAD"], cwd=path).stdout.rstrip("\n")

    diff = run_git(["diff", "HEAD"], cwd=path).stdout

    untracked = [
        entry.path
        for entry in worktree_status(path)
        if entry.untracked and _should_track(entry.path)
    ]
    if untracked:
        # `git add -N` records intent-to-add so `git diff` renders the file as
        # an addition without staging its contents.
        run_git(["add", "--intent-to-add", "--", *untracked], cwd=path)
        diff = run_git(["diff", "HEAD"], cwd=path).stdout
        run_git(["reset", "--quiet", "--", *untracked], cwd=path)

    return diff.strip("\n")


def _require_base(path: Path, base: str) -> str:
    """Resolve ``base`` inside a worktree, or explain why it cannot be."""
    resolved = resolve_base(path, base)
    if resolved is None:
        raise GitCommandError(
            ("diff", base),
            128,
            f"base branch {base!r} does not exist in {path}",
        )
    return resolved


def _should_track(path: str) -> bool:
    """Ignore editor cruft and build output that is never worth committing."""
    ignored = (
        ".DS_Store",
        "Thumbs.db",
        ".idea/",
        ".vscode/",
        "__pycache__/",
        "*.pyc",
        ".env",
        # Scratch HOME created by apps.git.runner inside every worktree. It is
        # purely administrative (git identity/config) and must never be committed.
        ".codepilotai-home/",
    )
    return not any(path == item or path.startswith(item) or path.endswith(item) for item in ignored)


def list_local_branches(path: Path) -> list[str]:
    """Local branch names in a checkout, newest first by commit date."""
    return git_lines(
        ["for-each-ref", "--sort=-committerdate", "--format=%(refname:short)", "refs/heads"],
        cwd=path,
    )


def has_commits(path: Path) -> bool:
    """True when HEAD resolves to a commit (false in a freshly initialised tree)."""
    return run_git(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=path, check=False).ok


def cleanup_all_worktrees() -> int:
    """Remove every worktree on disk. Returns how many were deleted.

    Used by the janitor task when a repository has been deleted or a worker was
    killed mid-run, leaving directories that no ``AgentTask`` row points at.
    """
    root = worktree_root()
    if not root.exists():
        return 0
    removed = 0
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and entry.name.isdigit() and remove_worktree_by_task(int(entry.name)):
            removed += 1
    return removed


def orphaned_worktree_ids() -> list[int]:
    """Task ids that have a worktree directory but no owning task row."""
    from apps.agent_tasks.models import AgentTask

    root = worktree_root()
    if not root.exists():
        return []
    candidates = {
        int(entry.name) for entry in root.iterdir() if entry.is_dir() and entry.name.isdigit()
    }
    if not candidates:
        return []
    live = set(AgentTask.objects.filter(pk__in=candidates).values_list("pk", flat=True))
    return sorted(candidates - live)


__all__: list[str] = [
    "BRANCH_PREFIX",
    "StatusEntry",
    "branch_name_for",
    "changed_files",
    "cleanup_all_worktrees",
    "commit",
    "create_worktree",
    "has_commits",
    "is_valid_branch",
    "list_local_branches",
    "orphaned_worktree_ids",
    "push_branch",
    "remove_worktree",
    "remove_worktree_by_task",
    "resolve_base",
    "slugify",
    "stage_all",
    "uncommitted_diff",
    "worktree_is_clean",
    "worktree_path_for",
    "worktree_root",
    "worktree_status",
]

"""Bare mirrors: one per repository, shared by every task that targets it.

Why a mirror rather than per-task clones:

* A bare mirror is fetched once and then reused. Three tasks on the same
  repository cost one network round trip, not three.
* Pushing from a worktree attached to a mirror cannot rewrite the mirror's own
  history, so a bad agent run cannot corrupt the shared local copy.
* A mirror is not a working tree, so there is no index to leave dirty between
  tasks.

The workspace key is derived from the repository's ``full_name`` rather than
its primary key so that re-importing the same GitHub repository reuses the
existing mirror instead of cloning a second copy.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import time
from collections.abc import Callable
from pathlib import Path

from django.conf import settings

from apps.git.errors import MirrorError
from apps.git.runner import DEFAULT_TIMEOUT, git_lines, git_text, run_git
from apps.repositories.models import Repository
from config.observability import get_logger

logger = get_logger(__name__)

#: Fetch a mirror at most this often. Several tasks starting at once must not
#: each pay for a network round trip.
MIN_FETCH_INTERVAL_SECONDS = 30.0

#: ``owner__repo`` -> filesystem-safe. Anything outside this set is replaced, so
#: a hostile repository name cannot escape the mirror directory.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

CLONE_TIMEOUT = DEFAULT_TIMEOUT
FETCH_TIMEOUT = 300.0


def mirror_root() -> Path:
    """Root directory holding every bare mirror."""
    return Path(settings.WORKSPACE_ROOT) / "mirrors"


def mirror_path_for(full_name: str) -> Path:
    """Return the on-disk path of the mirror for ``owner/repo``.

    Raises if the sanitised name still contains a path separator, which would
    mean the allowlist above is wrong.
    """
    key = _UNSAFE.sub("-", full_name).strip("-")
    if not key or "/" in key or "\\" in key or key in {".", ".."}:
        raise MirrorError(f"repository name {full_name!r} cannot be mapped to a safe path")
    return mirror_root() / f"{key}.git"


def is_mirror_present(path: Path) -> bool:
    """True when ``path`` is a usable bare repository."""
    return (path / "HEAD").is_file() and (path / "objects").is_dir()


def ensure_mirror(
    repository: Repository,
    *,
    token: str,
    force: bool = False,
) -> Path:
    """Return a current bare mirror of ``repository``, cloning if necessary.

    ``token`` is required even for an existing mirror: a bare mirror carries the
    remote URL from clone time, and a token that has since been revoked must not
    be baked into a URL we might re-push to.
    """
    path = mirror_path_for(repository.full_name)
    path.parent.mkdir(parents=True, exist_ok=True)

    if force and path.exists():
        _remove_mirror(path)

    if is_mirror_present(path):
        update_mirror(path, token=token, repository=repository)
        return path

    if path.exists():
        # Present but not a repository: a half-finished clone from a previous
        # crash. Discard it rather than layering commands onto a broken state.
        logger.warning("mirror_incomplete_discarded", path=str(path))
        _remove_mirror(path)

    logger.info("mirror_cloning", repository=repository.full_name, path=str(path))
    url = remote_url(repository)
    logger.debug("mirror_cloning_from", url=url)
    try:
        run_git(
            ["clone", "--bare", "--quiet", url, str(path)],
            cwd=path.parent,
            token=token,
            timeout=CLONE_TIMEOUT,
        )
        _use_remote_tracking_refs(path)
        # `clone --bare` copies heads into refs/heads/* and installs no fetch
        # refspec at all, so nothing under refs/remotes/origin/* exists yet.
        # Repointing the refspec does not populate those refs by itself -- an
        # explicit fetch does, and since the objects already landed in the first
        # pass this costs a local ref update rather than a second download.
        update_mirror(path, token=token, repository=repository)
    except Exception:
        # The mirror may be half-written. Remove it so the next attempt starts
        # from a clean state instead of layering commands onto a broken repo.
        _remove_mirror(path)
        raise

    logger.info("mirror_cloned", repository=repository.full_name)
    return path


def _use_remote_tracking_refs(path: Path) -> None:
    """Repoint the mirror's fetch at ``refs/remotes/origin/*``.

    The default for a bare clone leaves the remote's heads copied into
    ``refs/heads/*`` with no fetch refspec at all, and ``git clone --mirror``'s
    ``+refs/*:refs/*`` keeps writing to them directly. Both are wrong here, and
    for the same reason: task worktrees live in this same repository, and
    ``refs/heads/*`` is exactly where their feature branches are.

    Under ``--mirror``, git refuses any ``git push origin <branch>:<branch>`` with
    "--mirror can't be combined with refspecs" -- the agent could clone but never
    deliver. Under the direct mapping with the mirror flag cleared, a fetch dies
    with "refusing to fetch into branch ... checked out at .../worktrees/N" the
    moment a second task starts, because it wants to update a branch the first
    task has checked out.

    Remote-tracking refs remove both problems at once: fetches never touch
    ``refs/heads/*``, pushes with a refspec work, and pruning can only delete
    stale ``refs/remotes/origin/*`` entries rather than another task's work.
    """
    run_git(
        ["config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"],
        cwd=path,
    )
    run_git(["config", "remote.origin.tagOpt", "--tags"], cwd=path)


def update_mirror(
    path: Path,
    *,
    token: str,
    repository: Repository | None = None,
    prune: bool = True,
) -> bool:
    """Fetch an existing mirror. Returns whether the fetch succeeded.

    A failed fetch is reported, not raised: a stale mirror is usually still good
    enough to branch from, and failing the whole task because GitHub was briefly
    unavailable would be worse than proceeding against the last known state. The
    caller can inspect the returned boolean where that trade-off is wrong.

    ``prune`` is safe because fetches land in ``refs/remotes/origin/*`` (see
    :func:`_use_remote_tracking_refs`). It can only delete refs the remote
    dropped, never a ``refs/heads/*`` branch that a task has checked out.
    """
    if not is_mirror_present(path):
        raise MirrorError(f"no bare mirror at {path}")

    # `git remote update` accepts only --prune; passing `--quiet` is a hard
    # error rather than a no-op.
    args = ["remote", "update", *(["--prune"] if prune else [])]
    result = run_git(
        args,
        cwd=path,
        token=token,
        timeout=FETCH_TIMEOUT,
        check=False,
    )
    if not result.ok:
        logger.warning(
            "mirror_fetch_failed",
            path=str(path),
            repository=repository.full_name if repository else None,
            stderr=result.stderr[:300],
        )
        return False
    return True


def mirror_needs_refresh(repository: Repository) -> bool:
    """True when the mirror is absent or older than the minimum fetch interval."""
    path = mirror_path_for(repository.full_name)
    if not is_mirror_present(path):
        return True
    age = _age_seconds(path)
    return age is None or age >= MIN_FETCH_INTERVAL_SECONDS


def remote_branch_ref(branch: str) -> str:
    """The ref a fetch maintains for ``branch``.

    Always prefer this over the bare name when branching or diffing. A bare clone
    copies the remote's heads into ``refs/heads/*`` where no fetch ever updates
    them again, so ``git rev-parse main`` happily returns a stale commit and the
    next task is built on code the repository already replaced.
    """
    return f"refs/remotes/origin/{branch}"


def resolve_ref(path: Path, ref: str) -> str | None:
    """Return the commit SHA a ref points at, or ``None`` if it does not exist."""
    result = run_git(
        ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=path,
        check=False,
    )
    sha = result.stdout.strip()
    if result.ok and re.fullmatch(r"[0-9a-f]{40}", sha):
        return sha
    return None


def list_remote_branches(path: Path, *, token: str | None = None) -> dict[str, str]:
    """Map branch name to head SHA for every branch the mirror knows about.

    Both ref namespaces are read. ``refs/remotes/origin/*`` is what a fetch
    maintains; ``refs/heads/*`` holds the feature branches the agent created and
    has not pushed yet, which the UI needs to show as still in progress.
    """
    lines = git_lines(
        [
            "for-each-ref",
            "--format=%(refname:short) %(objectname)",
            "refs/heads",
            "refs/remotes/origin",
        ],
        cwd=path,
        token=token,
    )
    branches: dict[str, str] = {}
    for line in lines:
        name, _, sha = line.partition(" ")
        name = name.removeprefix("origin/")
        if name and name != "HEAD" and re.fullmatch(r"[0-9a-f]{40}", sha):
            branches[name] = sha
    return branches


def set_mirror_head(path: Path, default_branch: str) -> None:
    """Point the mirror's HEAD at ``default_branch`` if that branch exists."""
    if resolve_ref(path, default_branch) is None:
        logger.warning("mirror_head_missing", branch=default_branch, path=str(path))
        return
    git_text(["symbolic-ref", "HEAD", f"refs/heads/{default_branch}"], cwd=path)


def _host() -> str:
    return str(settings.GITHUB_HOST).strip("/") or "github.com"


def remote_url(repository: Repository) -> str:
    """Build the remote URL for ``repository``.

    There is exactly one form, used for the initial clone *and* persisted as
    ``remote.origin.url``, and it has no credential slot. Two forms used to
    exist -- a credentialed clone URL and a clean remote URL -- but the
    credentialed one put a token into argv, where it is visible to any process on
    the machine and which git then quotes back inside fatal errors. Askpass
    supplies the token for every command instead, including the clone.
    """
    return settings.GITHUB_REMOTE_URL_TEMPLATE.format(
        username=settings.GITHUB_GIT_USERNAME,
        host=_host(),
        full_name=repository.full_name,
    )


def _remove_mirror(path: Path) -> None:
    """Delete a mirror directory tree, tolerating a locked or partial state."""

    def _on_error(func: Callable[..., object], target: str, _exc: object) -> None:
        # Windows keeps handles open briefly after git exits, and git marks
        # objects read-only. Clear the bit and retry once.
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            logger.warning("mirror_remove_failed", path=target)

    if path.exists():
        shutil.rmtree(path, onerror=_on_error)


def _age_seconds(path: Path) -> float | None:
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return None


__all__: list[str] = [
    "ensure_mirror",
    "is_mirror_present",
    "list_remote_branches",
    "mirror_needs_refresh",
    "mirror_path_for",
    "mirror_root",
    "remote_branch_ref",
    "remote_url",
    "resolve_ref",
    "set_mirror_head",
    "update_mirror",
]

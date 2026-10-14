"""Tests for the bare-mirror lifecycle against a local origin repository.

``origin`` is a real bare git repository in ``tmp_path``, and the URL templates
are pointed at it, so the production code path -- template substitution, clone,
fetch refspec wiring, fetch -- runs for real without touching the network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from apps.git import mirror
from apps.git.errors import MirrorError
from apps.git.runner import git_text, run_git
from apps.repositories.models import Repository


@pytest.fixture
def repo(repository: Repository) -> Repository:
    """The ``repository`` fixture, named for readability."""
    return repository


def test_mirror_path_is_derived_from_full_name(git_workspace: Any, repo: Repository) -> None:
    path = mirror.mirror_path_for(repo.full_name)

    assert path.name == "carol-demo-app.git"
    assert path.parent == mirror.mirror_root()
    assert mirror.mirror_root().is_relative_to(git_workspace.WORKSPACE_ROOT)


@pytest.mark.parametrize(
    "full_name",
    [
        "../../etc",
        "a/b/../../c",
        "..",
        ".",
        "///",
        "",
        "---",
    ],
)
def test_mirror_path_refuses_unsafe_full_names(full_name: str) -> None:
    # Either the name sanitises to something confined to the mirrors directory,
    # or it is refused outright. What must never happen is an escaping path.
    try:
        path = mirror.mirror_path_for(full_name)
    except MirrorError:
        return
    assert path.parent == mirror.mirror_root()


def test_ensure_mirror_clones_the_origin(git_workspace: Any, repo: Repository) -> None:
    path = mirror.ensure_mirror(repo, token="")

    assert mirror.is_mirror_present(path)
    assert mirror.resolve_ref(path, mirror.remote_branch_ref("main")) is not None
    assert git_text(["config", "--get", "remote.origin.url"], cwd=path).endswith(
        "carol/demo-app.git"
    )


def test_ensure_mirror_is_idempotent(git_workspace: Any, repo: Repository) -> None:
    first = mirror.ensure_mirror(repo, token="")
    head = mirror.resolve_ref(first, mirror.remote_branch_ref("main"))

    second = mirror.ensure_mirror(repo, token="")

    assert first == second
    assert mirror.resolve_ref(second, mirror.remote_branch_ref("main")) == head


def test_force_reclones_from_scratch(git_workspace: Any, repo: Repository) -> None:
    path = mirror.ensure_mirror(repo, token="")
    git_text(["branch", "scratch", "main"], cwd=path)

    mirror.ensure_mirror(repo, token="", force=True)

    assert mirror.resolve_ref(path, "scratch") is None


def test_prune_mirror_never_touches_a_local_feature_branch(
    git_workspace: Any, repo: Repository
) -> None:
    # The concurrency guarantee. A mirror's fetch refspec targets
    # refs/remotes/origin/*, so --prune can only remove stale remote-tracking
    # refs. Under git's default direct mapping it would instead delete the
    # feature branch another task is midway through creating.
    path = mirror.ensure_mirror(repo, token="")
    git_text(["branch", "codepilot/in-flight", "main"], cwd=path)

    assert mirror.update_mirror(path, token="", prune=True) is True

    assert mirror.resolve_ref(path, "codepilot/in-flight") is not None


def test_prune_removes_remote_tracking_refs_the_remote_dropped(
    git_workspace: Any, repo: Repository, origin: Path, origin_seed: Path, git_cli: Any
) -> None:
    path = mirror.ensure_mirror(repo, token="")

    git_cli(["branch", "feature", "main"], cwd=origin_seed)
    git_cli(["push", "--quiet", str(origin), "feature"], cwd=origin_seed)
    assert mirror.update_mirror(path, token="") is True
    assert "feature" in mirror.list_remote_branches(path)

    git_cli(["push", "--quiet", str(origin), "--delete", "feature"], cwd=origin_seed)

    assert mirror.update_mirror(path, token="", prune=True) is True
    assert "feature" not in mirror.list_remote_branches(path)


def test_mirror_accepts_a_refspec_push(git_workspace: Any, repo: Repository) -> None:
    # `clone --mirror` sets remote.origin.mirror=true, under which git refuses
    # every `git push origin <branch>:<branch>` with "--mirror can't be combined
    # with refspecs". Without this the agent could clone but never deliver.
    path = mirror.ensure_mirror(repo, token="")

    mirror_flag = run_git(
        ["config", "--get", "remote.origin.mirror"],
        cwd=path,
        check=False,
    ).stdout.strip()

    assert mirror_flag != "true"


def test_mirror_fetches_into_remote_tracking_refs(git_workspace: Any, repo: Repository) -> None:
    path = mirror.ensure_mirror(repo, token="")

    fetchspec = git_text(["config", "--get", "remote.origin.fetch"], cwd=path)

    assert fetchspec == "+refs/heads/*:refs/remotes/origin/*"


def test_remote_branch_ref_is_preferred_over_the_bare_name(
    git_workspace: Any, repo: Repository
) -> None:
    path = mirror.ensure_mirror(repo, token="")

    # Both resolve, but only the remote-tracking ref is maintained by a fetch.
    assert mirror.resolve_ref(path, "main") is not None
    assert mirror.resolve_ref(path, mirror.remote_branch_ref("main")) is not None
    assert mirror.remote_branch_ref("main") == "refs/remotes/origin/main"


def test_mirror_tracks_the_remote_default_branch(
    git_workspace: Any, repo: Repository, origin: Path, origin_seed: Path, git_cli: Any
) -> None:
    path = mirror.ensure_mirror(repo, token="")
    before = mirror.resolve_ref(path, mirror.remote_branch_ref("main"))
    assert before is not None

    git_cli(["remote", "add", "origin", str(origin)], cwd=origin_seed)
    (origin_seed / "new.py").write_text("x = 1\n", encoding="utf-8")
    git_cli(["add", "--all"], cwd=origin_seed)
    git_cli(["commit", "--quiet", "-m", "Move main forward"], cwd=origin_seed)
    git_cli(["push", "--quiet", "origin", "main"], cwd=origin_seed)

    assert mirror.update_mirror(path, token="") is True
    after = mirror.resolve_ref(path, mirror.remote_branch_ref("main"))
    assert after is not None
    assert after != before


def test_clone_failure_leaves_nothing_behind(git_workspace: Any, repo: Repository) -> None:
    repo.full_name = "carol/does-not-exist"
    path = mirror.mirror_path_for(repo.full_name)

    with pytest.raises(Exception):  # noqa: B017, PT011 - git's type is not ours
        mirror.ensure_mirror(repo, token="")

    # A half-cloned mirror must not be mistaken for a usable one on the next run.
    assert not path.exists() or not mirror.is_mirror_present(path)


def test_ensure_mirror_on_an_empty_remote_succeeds(
    git_workspace: Any, repo: Repository, tmp_path: Path, git_cli: Any
) -> None:
    repo.full_name = "carol/empty-app"
    empty = tmp_path / "origin" / "carol" / "empty-app.git"
    empty.mkdir(parents=True)
    git_cli(["init", "--bare", "--quiet", str(empty)], cwd=tmp_path)

    path = mirror.ensure_mirror(repo, token="")

    assert mirror.is_mirror_present(path)
    assert mirror.resolve_ref(path, mirror.remote_branch_ref("main")) is None
    assert mirror.list_remote_branches(path) == {}


def test_update_mirror_reports_failure_without_raising(
    git_workspace: Any, repo: Repository
) -> None:
    path = mirror.ensure_mirror(repo, token="")
    git_text(
        ["remote", "set-url", "origin", str(Path(git_workspace.WORKSPACE_ROOT) / "gone.git")],
        cwd=path,
    )

    assert mirror.update_mirror(path, token="") is False


def test_mirror_never_persists_a_token_on_disk(git_workspace: Any, repo: Repository) -> None:
    token = "gho_supersecret_value"

    path = mirror.ensure_mirror(repo, token=token)

    # Nothing under the workspace may contain the token. A credential written
    # into <mirror>/config outlives the process, is readable by anything that
    # can read the disk, and is echoed by `git remote -v` and by fatal errors.
    offenders = [
        str(item.relative_to(path.parent))
        for item in path.parent.rglob("*")
        if item.is_file() and token in item.read_text(encoding="utf-8", errors="replace")
    ]
    assert offenders == []


def test_remote_url_substitutes_every_placeholder(git_workspace: Any, repo: Repository) -> None:
    git_workspace.GITHUB_REMOTE_URL_TEMPLATE = "https://{username}@{host}/{full_name}.git"

    url = mirror.remote_url(repo)

    assert url == f"https://x-access-token@{git_workspace.GITHUB_HOST}/carol/demo-app.git"
    assert "{" not in url


def test_remote_url_carries_no_token(git_workspace: Any, repo: Repository) -> None:
    url = mirror.remote_url(repo)

    assert "token" not in url.lower()
    assert url.endswith("carol/demo-app.git")


def test_update_mirror_requires_an_existing_mirror(git_workspace: Any, tmp_path: Path) -> None:
    with pytest.raises(MirrorError):
        mirror.update_mirror(tmp_path / "nope.git", token="")


def test_resolve_ref_returns_none_for_unknown_ref(git_workspace: Any, repo: Repository) -> None:
    path = mirror.ensure_mirror(repo, token="")

    assert mirror.resolve_ref(path, "main") is not None
    assert mirror.resolve_ref(path, "does-not-exist") is None
    assert mirror.resolve_ref(path, "main^{tree}") is None


def test_resolve_ref_does_not_treat_argument_injection_as_a_ref(
    git_workspace: Any, repo: Repository
) -> None:
    path = mirror.ensure_mirror(repo, token="")

    # `--verify` makes everything after it a rev, so a leading dash cannot turn
    # into an option. Assert the guard actually holds rather than trusting it.
    assert mirror.resolve_ref(path, "--output=/tmp/pwned") is None


def test_list_remote_branches_excludes_head(git_workspace: Any, repo: Repository) -> None:
    path = mirror.ensure_mirror(repo, token="")

    branches = mirror.list_remote_branches(path)

    assert "main" in branches
    assert "HEAD" not in branches
    assert all(len(sha) == 40 for sha in branches.values())


def test_list_remote_branches_includes_unpushed_feature_branches(
    git_workspace: Any, repo: Repository
) -> None:
    path = mirror.ensure_mirror(repo, token="")
    git_text(["branch", "codepilot/in-flight", "main"], cwd=path)

    assert "codepilot/in-flight" in mirror.list_remote_branches(path)


def test_set_mirror_head_points_at_an_existing_branch(git_workspace: Any, repo: Repository) -> None:
    path = mirror.ensure_mirror(repo, token="")

    mirror.set_mirror_head(path, "main")

    assert git_text(["symbolic-ref", "HEAD"], cwd=path) == "refs/heads/main"


def test_set_mirror_head_ignores_a_missing_branch(git_workspace: Any, repo: Repository) -> None:
    path = mirror.ensure_mirror(repo, token="")
    before = git_text(["symbolic-ref", "HEAD"], cwd=path)

    mirror.set_mirror_head(path, "nope")

    assert git_text(["symbolic-ref", "HEAD"], cwd=path) == before


def test_incomplete_mirror_is_discarded_and_recloned(git_workspace: Any, repo: Repository) -> None:
    path = mirror.mirror_path_for(repo.full_name)
    path.mkdir(parents=True)
    (path / "not-a-repo.txt").write_text("junk", encoding="utf-8")

    assert mirror.is_mirror_present(path) is False

    result = mirror.ensure_mirror(repo, token="")

    assert mirror.is_mirror_present(result)


def test_is_mirror_present_is_false_for_an_empty_directory(tmp_path: Path) -> None:
    empty = tmp_path / "empty.git"
    empty.mkdir()

    assert mirror.is_mirror_present(empty) is False
    assert mirror.is_mirror_present(tmp_path / "absent") is False


def test_mirror_needs_refresh_tracks_a_missing_clone(git_workspace: Any, repo: Repository) -> None:
    assert mirror.mirror_needs_refresh(repo) is True

    mirror.ensure_mirror(repo, token="")

    assert mirror.mirror_needs_refresh(repo) is False

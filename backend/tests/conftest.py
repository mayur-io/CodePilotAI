"""Shared pytest fixtures for the CodePilotAI backend test suite."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.agent.errors import ProviderError
from apps.agent.providers.base import Completion, ToolCall, Usage
from apps.agent_tasks.models import AgentTask
from apps.repositories.models import Branch, Repository

User = get_user_model()

GITHUB_TOKEN = "gho_testtoken_do_not_log_me"


@pytest.fixture
def user(db: Any) -> Any:
    """A plain, GitHub-disconnected user."""
    return User.objects.create_user(username="alice", email="alice@example.com")


@pytest.fixture
def other_user(db: Any) -> Any:
    """A second user, used to verify cross-tenant isolation."""
    return User.objects.create_user(username="bob", email="bob@example.com")


@pytest.fixture
def connected_user(db: Any) -> Any:
    """A user with a stored, encrypted GitHub token."""
    account = User.objects.create_user(username="carol", email="carol@example.com")
    account.store_github_token(access_token=GITHUB_TOKEN, scope="repo read:user")
    account.save()
    return account


GITHUB_API = "https://api.github.com"


@pytest.fixture(scope="session")
def github_api() -> str:
    """Base URL of the GitHub REST API, for readability in tests."""
    return GITHUB_API


@pytest.fixture
def any_query() -> Callable[[str], re.Pattern[str]]:
    """Return a factory that matches a URL with or without a query string.

    ``pytest-httpx`` matches ``url`` literally, so a registered
    ``https://api.github.com/user/repos`` will not match the real request that
    carries ``?per_page=100&page=1``. Every GitHub client call in this codebase
    paginates, so tests match on the path and let the query through.
    """

    def factory(url: str) -> re.Pattern[str]:
        return re.compile(rf"^{re.escape(url)}(\?.*)?$")

    return factory


@pytest.fixture
def request_count(httpx_mock: Any) -> Callable[[], int]:
    """Return how many requests the mock has captured."""
    return lambda: len(httpx_mock.get_requests())


@pytest.fixture
def sent_json(httpx_mock: Any) -> Callable[..., Any]:
    """Return a callable that decodes the JSON body of a captured request."""

    def decode(index: int = 0) -> Any:
        return json.loads(httpx_mock.get_requests()[index].content)

    return decode


@pytest.fixture
def no_sleep() -> None:
    """Neutralise retry backoff so retry tests do not spend real seconds.

    Applied by ``usefixtures`` rather than autouse, so only the tests that
    exercise retry paths pay for it.
    """
    with patch("apps.github.client.time.sleep"):
        yield


@pytest.fixture
def api_client() -> APIClient:
    """An unauthenticated API client."""
    return APIClient()


@pytest.fixture
def auth_client(user: Any) -> APIClient:
    """An API client authenticated as ``user`` via JWT."""
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


@pytest.fixture
def connected_auth_client(connected_user: Any) -> APIClient:
    """An API client authenticated as ``connected_user``."""
    client = APIClient()
    token = RefreshToken.for_user(connected_user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


@pytest.fixture
def repository(connected_user: Any) -> Repository:
    """A repository belonging to ``connected_user``."""
    return Repository.objects.create(
        user=connected_user,
        github_id=4242,
        name="demo-app",
        full_name="carol/demo-app",
        default_branch="main",
        primary_language="Python",
        html_url="https://github.com/carol/demo-app",
    )


@pytest.fixture
def other_repository(other_user: Any) -> Repository:
    """A repository belonging to ``other_user``."""
    return Repository.objects.create(
        user=other_user,
        github_id=7777,
        name="secret-app",
        full_name="bob/secret-app",
        default_branch="main",
    )


@pytest.fixture
def task(repository: Repository, connected_user: Any) -> AgentTask:
    """A queued agent task with a feature branch already allocated."""
    task = AgentTask.objects.create(
        user=connected_user,
        repository=repository,
        base_branch="main",
        feature_branch="codepilot/add-login-form",
        prompt="Add a login form to the settings page.",
        title="Add login form",
        status=AgentTask.Status.QUEUED,
        phase=AgentTask.Phase.QUEUED,
    )
    Branch.objects.create(
        repository=repository,
        name=task.feature_branch,
        kind=Branch.Kind.FEATURE,
    )
    return task


# ── git plumbing ─────────────────────────────────────────────────────────────
# These fixtures build a *real* git repository in a temp directory and stand it
# in for GitHub. Nothing here goes through apps.git.runner, so the tests exercise
# the code under test rather than a shared helper that could hide a bug.

_GIT_IDENTITY = (
    "-c",
    "user.name=CodePilotAI Test",
    "-c",
    "user.email=test@codepilotai.invalid",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "init.defaultBranch=main",
)

_GIT_ENV_PASSTHROUGH = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP")


@pytest.fixture
def git_cli() -> Callable[..., str]:
    """Run plain git with a fixed identity, bypassing the project's hardening.

    Returns stdout. Raises ``CalledProcessError`` on failure so a broken fixture
    reports the git error instead of cascading into a confusing test failure.
    """

    def run(args: list[str], *, cwd: Path) -> str:
        completed = subprocess.run(
            ["git", *_GIT_IDENTITY, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            stdin=subprocess.DEVNULL,
            env=_clean_env(),
        )
        return completed.stdout

    return run


def _clean_env() -> dict[str, str]:
    """Environment for fixture git calls: no inherited ``GIT_*`` leakage."""
    env = {key: os.environ[key] for key in _GIT_ENV_PASSTHROUGH if key in os.environ}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["LC_ALL"] = "C"
    return env


@pytest.fixture
def origin_seed(git_cli: Callable[..., str], tmp_path: Path) -> Path:
    """A normal working clone with one commit, used to mutate the origin."""
    seed = tmp_path / "seed"
    git_cli(["init", "--quiet", str(seed)], cwd=tmp_path)
    (seed / "README.md").write_text("# demo-app\n\nOriginal content.\n", encoding="utf-8")
    (seed / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    git_cli(["add", "--all"], cwd=seed)
    git_cli(["commit", "--quiet", "-m", "Initial commit"], cwd=seed)
    return seed


@pytest.fixture
def origin(git_cli: Callable[..., str], tmp_path: Path, origin_seed: Path) -> Path:
    """A bare repository at the path the ``repository`` fixture's URL resolves to.

    Contains one commit on ``main`` so a mirror clone has something to check out.
    """
    bare = tmp_path / "origin" / "carol" / "demo-app.git"
    bare.mkdir(parents=True)
    git_cli(["init", "--bare", "--quiet", str(bare)], cwd=tmp_path)
    git_cli(["push", "--quiet", str(bare), "main"], cwd=origin_seed)
    return bare


@pytest.fixture
def git_workspace(settings: Any, tmp_path: Path, origin: Path) -> Any:
    """Point the git layer at ``origin`` and an isolated workspace root.

    Overrides both URL templates so the production substitution code runs for
    real, rather than stubbing out the module under test.
    """
    settings.WORKSPACE_ROOT = tmp_path / "workspace"
    settings.GITHUB_HOST = str(tmp_path / "origin")
    settings.GITHUB_REMOTE_URL_TEMPLATE = "{host}/{full_name}.git"
    settings.GITHUB_GIT_USERNAME = "x-access-token"
    settings.GIT_ALLOW_LOCAL_REMOTE = True
    settings.GIT_AUTHOR_NAME = "CodePilotAI"
    settings.GIT_AUTHOR_EMAIL = "agent@codepilotai.invalid"
    # The test environment has no user git identity; commits would fail without
    # these, and we do not want to touch the developer's real config.
    settings.GIT_CONFIG_GLOBAL = str(tmp_path / "empty-gitconfig")
    return settings


# ── agent.graph test doubles ─────────────────────────────────────────────────


class ScriptedProvider:
    """Plays back parsed completions without a socket.

    A test that does not converge must fail loudly rather than getting a silent
    empty completion, so running the script dry raises ProviderError.
    """

    def __init__(self, completions: list[Completion]) -> None:
        self.queue = list(completions)
        self.calls: list[list[Any]] = []
        self.kwargs: list[dict[str, Any]] = []

    def complete(
        self,
        messages: Sequence[Any],
        *,
        tools: list[dict[str, Any]] | None = None,
        reasoning_effort: str | None = None,
        max_tokens: int | None = None,
    ) -> Completion:
        self.calls.append(list(messages))
        self.kwargs.append(
            {"tools": tools, "reasoning_effort": reasoning_effort, "max_tokens": max_tokens}
        )
        if not self.queue:
            raise ProviderError("ScriptedProvider ran out of scripted completions")
        return self.queue.pop(0)


@pytest.fixture
def scripted_provider() -> Callable[[list[Completion]], ScriptedProvider]:
    def build(completions: list[Completion]) -> ScriptedProvider:
        return ScriptedProvider(completions)

    return build


@pytest.fixture
def tool_completion() -> Callable[..., Completion]:
    """A Completion whose single tool call targets ``name`` with ``arguments``."""

    def build(
        name: str,
        arguments: dict[str, Any],
        *,
        call_id: str = "call_1",
        content: str = "",
        reasoning: str = "",
    ) -> Completion:
        return Completion(
            content=content,
            tool_calls=(ToolCall(id=call_id, name=name, arguments=arguments),),
            reasoning=reasoning,
            usage=Usage(input_tokens=6, output_tokens=4),
        )

    return build


@pytest.fixture
def answer_completion() -> Callable[..., Completion]:
    """A plain-text Completion that ends a phase."""

    def build(text: str = "That's everything I need.", reasoning: str = "") -> Completion:
        return Completion(
            content=text,
            reasoning=reasoning,
            usage=Usage(input_tokens=6, output_tokens=4),
        )

    return build

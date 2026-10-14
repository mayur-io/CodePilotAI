"""The staff operations dashboard: gating, listing, filtering, and detail."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model

from apps.agent_tasks.models import AgentTask, ProposedChange, RunEvent

User = get_user_model()


@pytest.fixture
def staff_user(db: Any) -> Any:
    """A staff account that may sign in to the dashboard."""
    return User.objects.create_user(username="ops", email="ops@example.com", is_staff=True)


@pytest.fixture
def staff_client(client: Any, staff_user: Any) -> Any:
    """A session-authenticated client for ``staff_user``."""
    client.force_login(staff_user)
    return client


def _body(response: Any) -> str:
    return response.content.decode()


def test_anonymous_is_sent_to_login(client: Any) -> None:
    response = client.get("/dashboard/")
    assert response.status_code == 302
    assert "/dashboard/login/" in response["Location"]


def test_non_staff_is_sent_to_login(client: Any, user: Any) -> None:
    client.force_login(user)
    response = client.get("/dashboard/")
    assert response.status_code == 302
    assert "/dashboard/login/" in response["Location"]


def test_login_page_renders(client: Any, db: Any) -> None:
    # LoginView's context includes the current Site, so this needs the database.
    response = client.get("/dashboard/login/")
    assert response.status_code == 200
    assert "Sign in" in _body(response)


def test_staff_can_sign_in_with_password(client: Any, staff_user: Any) -> None:
    staff_user.set_password("correct-horse-battery")
    staff_user.save()
    response = client.post(
        "/dashboard/login/",
        {"username": staff_user.username, "password": "correct-horse-battery"},
    )
    assert response.status_code == 302
    assert response["Location"] == "/dashboard/"


def test_logout_via_post(staff_client: Any) -> None:
    response = staff_client.post("/dashboard/logout/")
    assert response.status_code == 302


def test_home_lists_platform_wide_tasks(
    staff_client: Any, task: AgentTask, other_repository: Any, other_user: Any
) -> None:
    AgentTask.objects.create(
        user=other_user, repository=other_repository, prompt="Secret work", title="Other work"
    )
    response = staff_client.get("/dashboard/")
    assert response.status_code == 200
    body = _body(response)
    assert task.repository.full_name in body
    # The console is an internal ops surface: it shows every user's tasks.
    assert "Other work" in body


def test_task_list_filters_by_status(staff_client: Any, task: AgentTask) -> None:
    AgentTask.objects.create(
        user=task.user,
        repository=task.repository,
        prompt="Fail me",
        title="Broken task",
        status=AgentTask.Status.FAILED,
    )
    body = _body(staff_client.get("/dashboard/tasks/?status=failed"))
    assert "Broken task" in body
    assert task.title not in body


def test_task_list_searches_title_and_prompt(staff_client: Any, task: AgentTask) -> None:
    assert task.title in _body(staff_client.get("/dashboard/tasks/?q=login"))
    assert "No tasks match." in _body(staff_client.get("/dashboard/tasks/?q=zzznope"))


def test_task_detail_shows_run_artifacts(staff_client: Any, task: AgentTask) -> None:
    task.pr_number = 7
    task.pr_url = "https://github.com/carol/demo-app/pull/7"
    task.verification = {"passed": True, "outcomes": [{"command": "pytest -q", "passed": True}]}
    task.diff_stat = {"files": 2, "additions": 10, "deletions": 3}
    task.save()
    ProposedChange.objects.create(
        task=task,
        path="app/views.py",
        change_type=ProposedChange.ChangeType.MODIFY,
        patch="@@ -1 +1 @@\n-a\n+b\n",
        additions=1,
        deletions=1,
    )
    RunEvent.objects.create(task=task, seq=1, kind=RunEvent.Kind.PHASE, payload={"phase": "verify"})

    response = staff_client.get(f"/dashboard/tasks/{task.pk}/")
    assert response.status_code == 200
    body = _body(response)
    assert task.prompt in body
    assert "pull/7" in body
    assert "pytest -q" in body
    assert "app/views.py" in body
    assert "verify" in body


def test_task_detail_404_for_unknown_task(staff_client: Any) -> None:
    response = staff_client.get("/dashboard/tasks/999999/")
    assert response.status_code == 404

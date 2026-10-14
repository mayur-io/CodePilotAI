"""Tests for the agent task API: creation, branch allocation, approvals, messages."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.agent_tasks import usage
from apps.agent_tasks.models import AgentTask, Message, ProposedChange, RunEvent
from apps.repositories.models import Branch, Repository

pytestmark = pytest.mark.django_db


def make_task(user: Any, repository: Repository, **overrides: Any) -> AgentTask:
    """Create a task owned by ``user``, bypassing the API."""
    defaults: dict[str, Any] = {
        "user": user,
        "repository": repository,
        "prompt": "A task",
        "feature_branch": "codepilot/a-task",
    }
    defaults.update(overrides)
    return AgentTask.objects.create(**defaults)


class TestTaskCreation:
    def test_task_is_created_queued_with_a_feature_branch(
        self,
        connected_auth_client: APIClient,
        repository: Repository,
    ) -> None:
        response = connected_auth_client.post(
            reverse("v1:agent-task-list"),
            {"repository_id": repository.pk, "prompt": "Add a login form"},
            format="json",
        )

        assert response.status_code == 201, response.data
        task = AgentTask.objects.get()
        assert task.status == AgentTask.Status.QUEUED
        assert task.base_branch == "main"
        assert task.feature_branch.startswith("codepilot/")
        assert task.repository_id == repository.pk

    def test_branch_name_is_derived_from_the_prompt(
        self, connected_auth_client: APIClient, repository: Repository
    ) -> None:
        connected_auth_client.post(
            reverse("v1:agent-task-list"),
            {"repository_id": repository.pk, "prompt": "Add a login form"},
            format="json",
        )

        task = AgentTask.objects.get()
        assert task.feature_branch == "codepilot/add-a-login-form"

    def test_duplicate_slugs_get_a_numeric_suffix(
        self, connected_auth_client: APIClient, repository: Repository
    ) -> None:
        payload = {"repository_id": repository.pk, "prompt": "Add a login form"}

        first = connected_auth_client.post(reverse("v1:agent-task-list"), payload, format="json")
        second = connected_auth_client.post(reverse("v1:agent-task-list"), payload, format="json")

        assert first.status_code == 201
        assert second.status_code == 201
        assert (
            AgentTask.objects.get(pk=first.data["id"]).feature_branch
            == "codepilot/add-a-login-form"
        )
        assert (
            AgentTask.objects.get(pk=second.data["id"]).feature_branch
            == "codepilot/add-a-login-form-2"
        )

    def test_a_branch_row_is_created_for_the_feature_branch(
        self, connected_auth_client: APIClient, repository: Repository
    ) -> None:
        response = connected_auth_client.post(
            reverse("v1:agent-task-list"),
            {"repository_id": repository.pk, "prompt": "Fix the typo"},
            format="json",
        )

        branch = Branch.objects.get(repository=repository, name=response.data["feature_branch"])
        assert branch.kind == Branch.Kind.FEATURE

    def test_explicit_base_branch_is_honoured(
        self, connected_auth_client: APIClient, repository: Repository
    ) -> None:
        response = connected_auth_client.post(
            reverse("v1:agent-task-list"),
            {
                "repository_id": repository.pk,
                "prompt": "Fix the typo",
                "base_branch": "develop",
            },
            format="json",
        )

        assert response.data["base_branch"] == "develop"

    def test_explicit_title_is_used_for_the_slug(
        self, connected_auth_client: APIClient, repository: Repository
    ) -> None:
        response = connected_auth_client.post(
            reverse("v1:agent-task-list"),
            {
                "repository_id": repository.pk,
                "prompt": "Something vague",
                "title": "Auth Middleware",
            },
            format="json",
        )

        assert response.data["feature_branch"] == "codepilot/auth-middleware"

    def test_task_count_is_incremented(
        self, connected_auth_client: APIClient, repository: Repository
    ) -> None:
        connected_auth_client.post(
            reverse("v1:agent-task-list"),
            {"repository_id": repository.pk, "prompt": "One"},
            format="json",
        )

        repository.refresh_from_db()
        assert repository.task_count == 1

    def test_creation_requires_an_authenticated_user(self, api_client: APIClient) -> None:
        response = api_client.post(
            reverse("v1:agent-task-list"),
            {"repository_id": 1, "prompt": "Add a login form"},
            format="json",
        )

        assert response.status_code == 401

    def test_creation_is_rejected_when_github_is_not_connected(
        self, auth_client: APIClient, user: Any
    ) -> None:
        # The user owns a repository but has disconnected GitHub, so the agent
        # has no credentials to push with.
        owned = Repository.objects.create(
            user=user, github_id=11, name="mine", full_name="alice/mine"
        )

        response = auth_client.post(
            reverse("v1:agent-task-list"),
            {"repository_id": owned.pk, "prompt": "Add a login form"},
            format="json",
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "github_not_connected"

    def test_no_task_row_is_left_behind_when_github_is_disconnected(
        self, auth_client: APIClient, user: Any
    ) -> None:
        owned = Repository.objects.create(
            user=user, github_id=12, name="mine", full_name="alice/mine"
        )

        auth_client.post(
            reverse("v1:agent-task-list"),
            {"repository_id": owned.pk, "prompt": "Add a login form"},
            format="json",
        )

        assert AgentTask.objects.count() == 0

    def test_prompt_is_required(
        self, connected_auth_client: APIClient, repository: Repository
    ) -> None:
        response = connected_auth_client.post(
            reverse("v1:agent-task-list"),
            {"repository_id": repository.pk},
            format="json",
        )

        assert response.status_code == 400

    def test_very_short_prompt_is_rejected(
        self, connected_auth_client: APIClient, repository: Repository
    ) -> None:
        response = connected_auth_client.post(
            reverse("v1:agent-task-list"),
            {"repository_id": repository.pk, "prompt": "x"},
            format="json",
        )

        assert response.status_code == 400


class TestTaskIsolation:
    def test_list_only_returns_the_callers_tasks(
        self,
        connected_auth_client: APIClient,
        task: AgentTask,
        other_user: Any,
    ) -> None:
        other_user_task = make_task(
            other_user,
            task.repository,
            prompt="Someone else's work",
            feature_branch="codepilot/not-mine",
        )

        response = connected_auth_client.get(reverse("v1:agent-task-list"))

        assert response.status_code == 200
        ids = {row["id"] for row in response.data["results"]}
        assert ids == {task.pk}
        assert other_user_task.pk not in ids

    def test_detail_of_another_users_task_is_404(
        self, connected_auth_client: APIClient, task: AgentTask, other_user: Any
    ) -> None:
        foreign = make_task(
            other_user,
            task.repository,
            prompt="Someone else's task",
            feature_branch="codepilot/not-mine",
        )

        response = connected_auth_client.get(reverse("v1:agent-task-detail", args=[foreign.pk]))

        assert response.status_code == 404

    def test_detail_of_an_unknown_task_is_404(
        self, connected_auth_client: APIClient, task: AgentTask
    ) -> None:
        assert (
            connected_auth_client.get(
                reverse("v1:agent-task-detail", args=[task.pk + 9999])
            ).status_code
            == 404
        )

    def test_task_cannot_be_created_against_another_users_repository(
        self, connected_auth_client: APIClient, other_repository: Repository
    ) -> None:
        response = connected_auth_client.post(
            reverse("v1:agent-task-list"),
            {"repository_id": other_repository.pk, "prompt": "Add a login form"},
            format="json",
        )

        assert response.status_code == 404


@pytest.fixture
def proposed_change(task: AgentTask) -> ProposedChange:
    """A single pending change awaiting the user's decision."""
    return ProposedChange.objects.create(
        task=task,
        order_index=0,
        path="app/views.py",
        change_type=ProposedChange.ChangeType.MODIFY,
        patch=(
            "--- a/app/views.py\n"
            "+++ b/app/views.py\n"
            "@@ -1,2 +1,3 @@\n"
            " def login(request):\n"
            "+    return render(request, 'login.html')\n"
        ),
        rationale="Render the login template.",
        additions=1,
        deletions=0,
    )


class TestChangeDecisions:
    def test_changes_are_listed_in_display_order(
        self, connected_auth_client: APIClient, task: AgentTask, proposed_change: ProposedChange
    ) -> None:
        second = ProposedChange.objects.create(
            task=task,
            order_index=1,
            path="app/templates/login.html",
            change_type=ProposedChange.ChangeType.CREATE,
            patch="--- /dev/null\n+++ b/app/templates/login.html\n",
        )

        response = connected_auth_client.get(reverse("v1:agent-task-changes", args=[task.pk]))

        assert response.status_code == 200
        assert [row["id"] for row in response.data] == [proposed_change.pk, second.pk]
        assert response.data[0]["patch"] == proposed_change.patch
        assert response.data[0]["is_decided"] is False

    def test_approving_marks_the_change_approved(
        self, connected_auth_client: APIClient, task: AgentTask, proposed_change: ProposedChange
    ) -> None:
        response = connected_auth_client.post(
            reverse("v1:agent-task-decide", args=[task.pk, proposed_change.pk]),
            {"decision": "approve"},
            format="json",
        )

        assert response.status_code == 200
        proposed_change.refresh_from_db()
        assert proposed_change.status == ProposedChange.Status.APPROVED
        assert proposed_change.is_decided is True
        assert proposed_change.decided_at is not None

    def test_rejecting_marks_the_change_rejected(
        self, connected_auth_client: APIClient, task: AgentTask, proposed_change: ProposedChange
    ) -> None:
        response = connected_auth_client.post(
            reverse("v1:agent-task-decide", args=[task.pk, proposed_change.pk]),
            {"decision": "reject"},
            format="json",
        )

        assert response.status_code == 200
        proposed_change.refresh_from_db()
        assert proposed_change.status == ProposedChange.Status.REJECTED

    def test_requesting_an_edit_keeps_the_change_pending_and_stores_feedback(
        self, connected_auth_client: APIClient, task: AgentTask, proposed_change: ProposedChange
    ) -> None:
        response = connected_auth_client.post(
            reverse("v1:agent-task-decide", args=[task.pk, proposed_change.pk]),
            {"decision": "request_edit", "feedback": "Use email, not username"},
            format="json",
        )

        assert response.status_code == 200
        proposed_change.refresh_from_db()
        assert proposed_change.status == ProposedChange.Status.PENDING
        assert proposed_change.user_feedback == "Use email, not username"

    def test_requesting_an_edit_without_feedback_is_rejected(
        self, connected_auth_client: APIClient, task: AgentTask, proposed_change: ProposedChange
    ) -> None:
        response = connected_auth_client.post(
            reverse("v1:agent-task-decide", args=[task.pk, proposed_change.pk]),
            {"decision": "request_edit", "feedback": "   "},
            format="json",
        )

        assert response.status_code == 400

    def test_unknown_decision_is_rejected(
        self, connected_auth_client: APIClient, task: AgentTask, proposed_change: ProposedChange
    ) -> None:
        response = connected_auth_client.post(
            reverse("v1:agent-task-decide", args=[task.pk, proposed_change.pk]),
            {"decision": "delete_everything"},
            format="json",
        )

        assert response.status_code == 400

    def test_decision_emits_an_event(
        self, connected_auth_client: APIClient, task: AgentTask, proposed_change: ProposedChange
    ) -> None:
        connected_auth_client.post(
            reverse("v1:agent-task-decide", args=[task.pk, proposed_change.pk]),
            {"decision": "approve"},
            format="json",
        )

        event = task.events.filter(kind=RunEvent.Kind.CHANGE_DECIDED).get()
        assert event.payload["change_id"] == proposed_change.pk
        assert event.payload["decision"] == "approve"

    def test_cannot_decide_on_another_users_task(
        self,
        connected_auth_client: APIClient,
        task: AgentTask,
        proposed_change: ProposedChange,
        other_user: Any,
    ) -> None:
        # A task belonging to someone else must 404, not 403: never confirm that
        # another tenant's change exists.
        foreign_task = make_task(
            other_user,
            task.repository,
            prompt="Someone else's task",
            feature_branch="codepilot/not-mine",
        )

        response = connected_auth_client.post(
            reverse("v1:agent-task-decide", args=[foreign_task.pk, proposed_change.pk]),
            {"decision": "approve"},
            format="json",
        )

        assert response.status_code == 404
        proposed_change.refresh_from_db()
        assert proposed_change.status == ProposedChange.Status.PENDING

    def test_change_from_another_task_cannot_be_decided(
        self, connected_auth_client: APIClient, task: AgentTask, proposed_change: ProposedChange
    ) -> None:
        # The change id must belong to the task in the URL.
        stray = ProposedChange.objects.create(
            task=task,
            order_index=1,
            path="app/other.py",
            change_type=ProposedChange.ChangeType.MODIFY,
            patch="--- a/app/other.py\n+++ b/app/other.py\n",
        )

        response = connected_auth_client.post(
            reverse("v1:agent-task-decide", args=[task.pk, stray.pk]),
            {"decision": "approve"},
            format="json",
        )

        assert response.status_code in {200, 400}
        if response.status_code == 200:
            stray.refresh_from_db()
            assert stray.task_id == task.pk


class TestMessages:
    def test_thread_is_readable(self, connected_auth_client: APIClient, task: AgentTask) -> None:
        Message.objects.create(task=task, role=Message.Role.USER, content="Add a login form")
        Message.objects.create(
            task=task,
            role=Message.Role.ASSISTANT,
            content="Added a login form in app/views.py.",
        )

        response = connected_auth_client.get(reverse("v1:agent-task-messages", args=[task.pk]))

        assert response.status_code == 200
        assert [row["role"] for row in response.data] == ["user", "assistant"]

    def test_follow_up_is_recorded_and_requeues_the_task(
        self, connected_auth_client: APIClient, task: AgentTask
    ) -> None:
        response = connected_auth_client.post(
            reverse("v1:agent-task-messages", args=[task.pk]),
            {"content": "Actually use email, not username"},
            format="json",
        )

        assert response.status_code == 201
        task.refresh_from_db()
        assert task.followup_count == 1
        assert task.status == AgentTask.Status.QUEUED
        assert Message.objects.filter(task=task, role=Message.Role.USER).count() == 1

    def test_follow_up_dispatches_a_new_run_when_the_pipeline_is_enabled(
        self,
        connected_auth_client: APIClient,
        task: AgentTask,
        monkeypatch: Any,
        settings: Any,
    ) -> None:
        settings.AGENT_PIPELINE_ENABLED = True
        sent: list[int] = []

        class FakeRunAgentTask:
            @staticmethod
            def delay(task_id: int) -> None:
                sent.append(task_id)

        monkeypatch.setattr("apps.agent.tasks.run_agent_task", FakeRunAgentTask)

        response = connected_auth_client.post(
            reverse("v1:agent-task-messages", args=[task.pk]),
            {"content": "Actually use email, not username"},
            format="json",
        )

        assert response.status_code == 201
        # A follow-up is a fresh run on the same branch, so it must reach a worker.
        assert sent == [task.pk]

    def test_follow_up_is_rejected_while_the_task_is_running(
        self, connected_auth_client: APIClient, task: AgentTask
    ) -> None:
        task.status = AgentTask.Status.RUNNING
        task.save(update_fields=["status"])

        response = connected_auth_client.post(
            reverse("v1:agent-task-messages", args=[task.pk]),
            {"content": "Actually use email"},
            format="json",
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "task_busy"

    def test_empty_follow_up_is_rejected(
        self, connected_auth_client: APIClient, task: AgentTask
    ) -> None:
        response = connected_auth_client.post(
            reverse("v1:agent-task-messages", args=[task.pk]),
            {"content": ""},
            format="json",
        )

        assert response.status_code == 400


class TestCancellation:
    def test_queued_task_can_be_cancelled(
        self, connected_auth_client: APIClient, task: AgentTask
    ) -> None:
        response = connected_auth_client.post(reverse("v1:agent-task-cancel", args=[task.pk]))

        assert response.status_code == 200
        task.refresh_from_db()
        assert task.status == AgentTask.Status.CANCELLED
        assert task.is_terminal is True
        assert task.finished_at is not None

    def test_already_pushed_task_is_not_reopened(
        self, connected_auth_client: APIClient, task: AgentTask
    ) -> None:
        task.status = AgentTask.Status.PUSHED
        task.save(update_fields=["status"])

        connected_auth_client.post(reverse("v1:agent-task-cancel", args=[task.pk]))

        task.refresh_from_db()
        assert task.status == AgentTask.Status.PUSHED

    def test_cancel_emits_a_terminal_event(
        self, connected_auth_client: APIClient, task: AgentTask
    ) -> None:
        connected_auth_client.post(reverse("v1:agent-task-cancel", args=[task.pk]))

        assert task.events.filter(kind=RunEvent.Kind.DONE).exists()


class TestEventHistory:
    def test_history_is_replayable_from_a_sequence_number(
        self, connected_auth_client: APIClient, task: AgentTask
    ) -> None:
        for index in range(3):
            RunEvent.objects.create(
                task=task,
                seq=index + 1,
                kind=RunEvent.Kind.LOG,
                payload={"index": index},
            )

        response = connected_auth_client.get(
            reverse("v1:agent-task-events-history", args=[task.pk]),
            {"after_seq": 1},
        )

        assert response.status_code == 200
        assert [row["seq"] for row in response.data] == [2, 3]

    def test_history_of_another_users_task_is_404(
        self, connected_auth_client: APIClient, task: AgentTask
    ) -> None:
        response = connected_auth_client.get(
            reverse("v1:agent-task-events-history", args=[task.pk + 9999])
        )

        assert response.status_code == 404


class TestPipelineGate:
    def test_creation_explains_why_no_run_started(
        self, connected_auth_client: APIClient, repository: Repository
    ) -> None:
        # AGENT_PIPELINE_ENABLED is off, so the task is queued and a log event
        # says so, rather than dispatching a run that cannot succeed.
        connected_auth_client.post(
            reverse("v1:agent-task-list"),
            {"repository_id": repository.pk, "prompt": "Add a login form"},
            format="json",
        )

        task = AgentTask.objects.get()
        log = task.events.filter(kind=RunEvent.Kind.LOG).get()
        assert "not enabled" in log.payload["message"]


def make_awaiting(task: AgentTask) -> AgentTask:
    """Advance a task to the decision gate the execute action requires."""
    task.status = AgentTask.Status.AWAITING_APPROVAL
    task.save(update_fields=["status", "updated_at"])
    return task


class TestExecute:
    def test_execute_requires_the_task_to_be_awaiting_approval(
        self, connected_auth_client: APIClient, task: AgentTask
    ) -> None:
        # The created task is still QUEUED; there is nothing to execute.
        response = connected_auth_client.post(reverse("v1:agent-task-execute", args=[task.pk]))

        assert response.status_code == 409
        assert response.data["error"]["code"] == "task_not_awaiting_approval"

    def test_execute_blocks_while_a_change_is_still_pending(
        self,
        connected_auth_client: APIClient,
        task: AgentTask,
        proposed_change: ProposedChange,
    ) -> None:
        clone = ProposedChange.objects.create(
            task=task,
            order_index=1,
            path="app/forms.py",
            change_type=ProposedChange.ChangeType.CREATE,
            patch=(
                "--- /dev/null\n+++ b/app/forms.py\n@@ -0,0 +1 @@\n+class LoginForm:\n+    pass\n"
            ),
            rationale="Second proposal.",
        )
        proposed_change.approve()
        proposed_change.save()
        clone.reject()
        clone.save()
        # Leave one more change pending so execution cannot start.
        ProposedChange.objects.create(
            task=task,
            order_index=2,
            path="app/models.py",
            change_type=ProposedChange.ChangeType.MODIFY,
            patch="--- a/app/models.py\n+++ b/app/models.py\n@@ -1 +1 @@\n-x\n+x\n",
            rationale="Undecided.",
        )
        make_awaiting(task)

        response = connected_auth_client.post(reverse("v1:agent-task-execute", args=[task.pk]))

        assert response.status_code == 409
        assert response.data["error"]["code"] == "changes_pending_decision"
        assert "1 change(s) still await" in response.data["error"]["message"]

    def test_execute_blocks_when_nothing_was_approved(
        self,
        connected_auth_client: APIClient,
        task: AgentTask,
        proposed_change: ProposedChange,
    ) -> None:
        proposed_change.reject()
        proposed_change.save()
        make_awaiting(task)

        response = connected_auth_client.post(reverse("v1:agent-task-execute", args=[task.pk]))

        assert response.status_code == 409
        assert response.data["error"]["code"] == "no_approved_changes"

    def test_execute_dispatches_to_the_worker_when_the_pipeline_is_enabled(
        self,
        connected_auth_client: APIClient,
        task: AgentTask,
        proposed_change: ProposedChange,
        monkeypatch: Any,
        settings: Any,
    ) -> None:
        proposed_change.approve()
        proposed_change.save()
        make_awaiting(task)
        settings.AGENT_PIPELINE_ENABLED = True
        sent: list[int] = []

        class FakeRunAgentTask:
            @staticmethod
            def delay(task_id: int) -> None:
                sent.append(task_id)

        monkeypatch.setattr("apps.agent.tasks.run_agent_task", FakeRunAgentTask)

        response = connected_auth_client.post(reverse("v1:agent-task-execute", args=[task.pk]))

        assert response.status_code == 200
        assert sent == [task.pk]
        # The task stays awaiting while the worker picks it up.
        task.refresh_from_db()
        assert task.status == AgentTask.Status.AWAITING_APPROVAL

    def test_execute_with_the_pipeline_disabled_only_logs_and_explains(
        self,
        connected_auth_client: APIClient,
        task: AgentTask,
        proposed_change: ProposedChange,
    ) -> None:
        proposed_change.approve()
        proposed_change.save()
        make_awaiting(task)
        # AGENT_PIPELINE_ENABLED is off in the test settings.

        response = connected_auth_client.post(reverse("v1:agent-task-execute", args=[task.pk]))

        assert response.status_code == 200
        log = task.events.filter(kind=RunEvent.Kind.LOG).get()
        assert "not enabled" in log.payload["message"]


class TestDailyBudget:
    """The per-user daily token cap is a hard stop on starting any new run."""

    @staticmethod
    def _spend(task: AgentTask, tokens: int, *, started_at: Any = None) -> None:
        task.started_at = started_at or timezone.now()
        task.input_tokens = tokens
        task.save(update_fields=["started_at", "input_tokens", "updated_at"])

    def test_daily_usage_only_counts_today(self, task: AgentTask, settings: Any) -> None:
        # Yesterday's run does not count against today's budget.
        self._spend(task, 500, started_at=timezone.now() - timedelta(days=1))
        assert usage.daily_usage(task.user).total_tokens == 0

        self._spend(task, 700)
        assert usage.daily_usage(task.user).total_tokens == 700

    def test_create_is_blocked_when_the_daily_budget_is_spent(
        self,
        connected_auth_client: APIClient,
        repository: Repository,
        task: AgentTask,
        settings: Any,
    ) -> None:
        self._spend(task, settings.AGENT_DAILY_TOKEN_BUDGET)

        response = connected_auth_client.post(
            reverse("v1:agent-task-list"),
            {"repository_id": repository.pk, "prompt": "One more thing"},
            format="json",
        )

        assert response.status_code == 429
        assert response.data["error"]["code"] == "daily_budget_exceeded"
        assert response.data["error"]["details"]["limit"] == settings.AGENT_DAILY_TOKEN_BUDGET
        # The rejected call must leave no orphaned queued task behind.
        assert AgentTask.objects.count() == 1

    def test_create_is_allowed_under_the_budget(
        self,
        connected_auth_client: APIClient,
        repository: Repository,
        task: AgentTask,
        settings: Any,
    ) -> None:
        self._spend(task, settings.AGENT_DAILY_TOKEN_BUDGET - 1)

        response = connected_auth_client.post(
            reverse("v1:agent-task-list"),
            {"repository_id": repository.pk, "prompt": "Still have budget"},
            format="json",
        )

        assert response.status_code == 201, response.data
        assert AgentTask.objects.count() == 2

    def test_execute_is_blocked_when_the_daily_budget_is_spent(
        self,
        connected_auth_client: APIClient,
        task: AgentTask,
        proposed_change: ProposedChange,
        settings: Any,
    ) -> None:
        proposed_change.approve(actor=task.user)
        proposed_change.save()
        make_awaiting(task)
        settings.AGENT_PIPELINE_ENABLED = True
        self._spend(task, settings.AGENT_DAILY_TOKEN_BUDGET)

        response = connected_auth_client.post(reverse("v1:agent-task-execute", args=[task.pk]))

        assert response.status_code == 429
        assert response.data["error"]["code"] == "daily_budget_exceeded"

    def test_follow_up_is_blocked_when_the_daily_budget_is_spent(
        self,
        connected_auth_client: APIClient,
        task: AgentTask,
        settings: Any,
    ) -> None:
        self._spend(task, settings.AGENT_DAILY_TOKEN_BUDGET)

        response = connected_auth_client.post(
            reverse("v1:agent-task-messages", args=[task.pk]),
            {"content": "One more change"},
            format="json",
        )

        assert response.status_code == 429
        # The gated request must not record the message or flip the status.
        task.refresh_from_db()
        assert task.status == AgentTask.Status.QUEUED
        assert Message.objects.filter(task=task).count() == 0


class TestTaskCostAccounting:
    """The task's cost column is priced from its tokens, cache discount included."""

    def test_record_usage_prices_tokens_and_the_cache(self, task: AgentTask) -> None:
        task.record_usage(input_tokens=1_000_000, output_tokens=0)
        assert task.cost_usd == Decimal("0.15")

        task.record_usage(input_tokens=1000, output_tokens=0, cached_tokens=1000)
        # 1M fresh input ($0.15) plus 1000 fully cached input at half rate.
        assert task.cost_usd == Decimal("0.15") + Decimal("0.000075")
        assert task.input_tokens == 1_001_000
        assert task.cached_tokens == 1000

    def test_daily_usage_cost_applies_the_cache_discount(self, task: AgentTask) -> None:
        task.started_at = timezone.now()
        task.input_tokens = 1_000_000
        task.cached_tokens = 1_000_000
        task.save(update_fields=["started_at", "input_tokens", "cached_tokens", "updated_at"])

        # A fully cached day of a million input tokens costs half of $0.15.
        assert usage.daily_usage(task.user).total_cost_usd == Decimal("0.0750")


class TestDecisionAudit:
    """Every approval/rejection is attributed to an actor with a timestamp."""

    def test_approval_records_the_actor(
        self,
        connected_auth_client: APIClient,
        connected_user: Any,
        task: AgentTask,
        proposed_change: ProposedChange,
    ) -> None:
        response = connected_auth_client.post(
            reverse("v1:agent-task-decide", args=[task.pk, proposed_change.pk]),
            {"decision": "approve"},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["decided_by_username"] == connected_user.username
        proposed_change.refresh_from_db()
        assert proposed_change.decided_by == connected_user
        assert proposed_change.decided_at is not None

    def test_rejection_records_the_actor(
        self,
        connected_auth_client: APIClient,
        connected_user: Any,
        task: AgentTask,
        proposed_change: ProposedChange,
    ) -> None:
        connected_auth_client.post(
            reverse("v1:agent-task-decide", args=[task.pk, proposed_change.pk]),
            {"decision": "reject"},
            format="json",
        )

        proposed_change.refresh_from_db()
        assert proposed_change.decided_by == connected_user
        assert proposed_change.decided_at is not None

    def test_decision_event_carries_the_actor_and_timestamp(
        self,
        connected_auth_client: APIClient,
        connected_user: Any,
        task: AgentTask,
        proposed_change: ProposedChange,
    ) -> None:
        connected_auth_client.post(
            reverse("v1:agent-task-decide", args=[task.pk, proposed_change.pk]),
            {"decision": "approve"},
            format="json",
        )

        event = task.events.filter(kind=RunEvent.Kind.CHANGE_DECIDED).get()
        assert event.payload["actor_id"] == connected_user.pk
        assert event.payload["actor_username"] == connected_user.username
        assert event.payload["decided_at"] is not None

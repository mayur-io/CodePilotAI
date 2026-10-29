"""Tests for the clarify phase: the agent asks before it writes.

Two things need proving here:

1. the controller only ever accepts well-formed questions (a choice question
   without options, a duplicate key, or a text question with options are model
   bugs that must come back as readable tool errors, never as a crash); and
2. the API round trip -- the task pauses in ``clarifying``, only a complete
   batch of answers resumes it, and the answers reach the next prompt as fixed
   decisions.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.agent.budget import Budget
from apps.agent.clarify import (
    MAX_QUESTIONS_PER_ROUND,
    ClarifyController,
    run_clarify,
)
from apps.agent.providers.base import ToolCall
from apps.agent_tasks import events
from apps.agent_tasks.models import AgentTask, Clarification, Message, RunEvent

pytestmark = pytest.mark.django_db


def budget() -> Budget:
    """A budget large enough that no clarify test ever hits a limit."""
    return Budget(token_limit=1_000_000, wall_clock_seconds=120)


def ask(controller: ClarifyController, **args: Any) -> Any:
    return controller.run(ToolCall(id="call_1", name="ask_questions", arguments=dict(args)))


def skip(controller: ClarifyController, reason: str = "Clear enough.") -> Any:
    return controller.run(ToolCall(id="call_2", name="skip", arguments={"reason": reason}))


def choice(key: str = "language", **overrides: Any) -> dict[str, Any]:
    """A well-formed single-choice question, with fields overridable."""
    question: dict[str, Any] = {
        "key": key,
        "question": "Which language should this be written in?",
        "kind": "single_choice",
        "options": [
            {"value": "python", "label": "Python"},
            {"value": "javascript", "label": "JavaScript"},
        ],
    }
    question.update(overrides)
    return question


class TestClarifyControllerValidation:
    def test_a_valid_batch_is_accepted(self) -> None:
        controller = ClarifyController()
        outcome = ask(controller, questions=[choice()])

        assert not outcome.result.is_error
        assert len(controller.questions) == 1
        assert controller.questions[0].key == "language"
        assert controller.questions[0].kind == "single_choice"

    def test_question_keys_are_normalised_into_slugs(self) -> None:
        controller = ClarifyController()
        ask(controller, questions=[choice(key="Auth Strategy")])

        assert controller.questions[0].key == "auth_strategy"

    def test_plain_string_options_are_accepted(self) -> None:
        controller = ClarifyController()
        ask(controller, questions=[choice(options=["Python", "JavaScript"])])

        assert [option["value"] for option in controller.questions[0].options] == [
            "Python",
            "JavaScript",
        ]

    def test_a_choice_question_needs_two_options(self) -> None:
        controller = ClarifyController()
        outcome = ask(controller, questions=[choice(options=[{"value": "x", "label": "X"}])])

        assert outcome.result.is_error
        assert "at least two options" in outcome.result.content
        assert controller.questions == ()

    def test_a_text_question_must_not_carry_options(self) -> None:
        controller = ClarifyController()
        outcome = ask(controller, questions=[choice(kind="text")])

        assert outcome.result.is_error
        assert "must not carry options" in outcome.result.content

    def test_a_text_question_needs_no_options(self) -> None:
        controller = ClarifyController()
        outcome = ask(
            controller,
            questions=[choice(kind="text", options=[], key="notes", question="Anything else?")],
        )

        assert not outcome.result.is_error
        assert controller.questions[0].kind == "text"

    def test_duplicate_keys_are_rejected(self) -> None:
        controller = ClarifyController()
        outcome = ask(controller, questions=[choice(), choice(key="language")])

        assert outcome.result.is_error
        assert "duplicate" in outcome.result.content

    def test_an_unknown_kind_is_rejected(self) -> None:
        controller = ClarifyController()
        outcome = ask(controller, questions=[choice(kind="dropdown")])

        assert outcome.result.is_error
        assert "kind must be one of" in outcome.result.content

    def test_an_empty_batch_is_rejected(self) -> None:
        controller = ClarifyController()
        outcome = ask(controller, questions=[])

        assert outcome.result.is_error

    def test_the_batch_is_capped_at_the_question_limit(self) -> None:
        controller = ClarifyController()
        many = [choice(key=f"q{index}") for index in range(MAX_QUESTIONS_PER_ROUND + 3)]
        ask(controller, questions=many)

        assert len(controller.questions) == MAX_QUESTIONS_PER_ROUND

    def test_an_unknown_tool_is_an_error_not_a_crash(self) -> None:
        controller = ClarifyController()
        outcome = controller.run(ToolCall(id="call_9", name="propose_change", arguments={}))

        assert outcome.result.is_error
        assert "Unknown tool" in outcome.result.content


class TestClarifySkip:
    def test_skip_marks_the_phase_as_skipped(self) -> None:
        controller = ClarifyController()
        outcome = skip(controller)

        assert not outcome.result.is_error
        assert controller.skipped
        assert controller.questions == ()

    def test_a_skip_result_does_not_pause(self) -> None:
        controller = ClarifyController()
        result = _run(controller, [("skip", {"reason": "Nothing to ask."})])

        assert not result.should_pause


class TestClarifyGraph:
    def test_a_question_batch_pauses_the_run(
        self, scripted_provider: Any, tool_completion: Any
    ) -> None:
        controller = ClarifyController()
        provider = scripted_provider(
            [tool_completion("ask_questions", {"questions": [choice()]})]
        )

        result = run_clarify(
            provider=provider,
            controller=controller,
            system=_system(),
            task=_human(),
            budget=budget(),
        )

        assert result.should_pause
        assert len(result.questions) == 1

    def test_the_tools_advertised_are_ask_and_skip(self) -> None:
        controller = ClarifyController()
        names = {schema["function"]["name"] for schema in controller.schemas()}

        assert names == {"ask_questions", "skip"}


class TestClarifyApi:
    def test_answers_resume_the_task(
        self, connected_auth_client: APIClient, repository: Any
    ) -> None:
        task = _paused_task(repository)
        question = _question(task)

        response = connected_auth_client.post(
            reverse("v1:agent-task-answer-questions", args=[task.pk]),
            {"answers": [{"id": question.pk, "answer": "python"}]},
            format="json",
        )

        assert response.status_code == 200, response.data
        task.refresh_from_db()
        question.refresh_from_db()
        assert question.answer == "python"
        assert question.is_answered
        assert task.status == AgentTask.Status.QUEUED
        assert not task.awaiting_answers

    def test_answering_writes_a_user_message_with_the_decisions(
        self, connected_auth_client: APIClient, repository: Any
    ) -> None:
        task = _paused_task(repository)
        question = _question(task)

        connected_auth_client.post(
            reverse("v1:agent-task-answer-questions", args=[task.pk]),
            {"answers": [{"id": question.pk, "answer": "python"}]},
            format="json",
        )

        message = Message.objects.get(task=task, role=Message.Role.USER)
        assert "Decisions already agreed" in message.content
        assert "python" in message.content

    def test_an_answered_event_is_emitted(
        self, connected_auth_client: APIClient, repository: Any
    ) -> None:
        task = _paused_task(repository)
        question = _question(task)

        connected_auth_client.post(
            reverse("v1:agent-task-answer-questions", args=[task.pk]),
            {"answers": [{"id": question.pk, "answer": "python"}]},
            format="json",
        )

        assert RunEvent.objects.filter(task=task, kind=RunEvent.Kind.ANSWERED).exists()

    def test_a_partial_batch_is_refused(
        self, connected_auth_client: APIClient, repository: Any
    ) -> None:
        task = _paused_task(repository)
        first = _question(task, key="language")
        _question(task, key="styling")

        response = connected_auth_client.post(
            reverse("v1:agent-task-answer-questions", args=[task.pk]),
            {"answers": [{"id": first.pk, "answer": "python"}]},
            format="json",
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "answers_incomplete"
        task.refresh_from_db()
        assert task.awaiting_answers

    def test_an_unknown_question_id_is_refused(
        self, connected_auth_client: APIClient, repository: Any
    ) -> None:
        task = _paused_task(repository)
        _question(task)

        response = connected_auth_client.post(
            reverse("v1:agent-task-answer-questions", args=[task.pk]),
            {"answers": [{"id": 999_999, "answer": "python"}]},
            format="json",
        )

        assert response.status_code == 400
        assert response.data["error"]["code"] == "unknown_question"

    def test_a_duplicate_answer_id_is_rejected(
        self, connected_auth_client: APIClient, repository: Any
    ) -> None:
        task = _paused_task(repository)
        question = _question(task)

        response = connected_auth_client.post(
            reverse("v1:agent-task-answer-questions", args=[task.pk]),
            {
                "answers": [
                    {"id": question.pk, "answer": "python"},
                    {"id": question.pk, "answer": "javascript"},
                ]
            },
            format="json",
        )

        assert response.status_code == 400
        assert response.data["error"]["details"]["answers"]

    def test_answering_a_task_that_is_not_paused_is_refused(
        self, connected_auth_client: APIClient, repository: Any
    ) -> None:
        task = _paused_task(repository)
        question = _question(task)
        task.status = AgentTask.Status.RUNNING
        task.save(update_fields=["status"])

        response = connected_auth_client.post(
            reverse("v1:agent-task-answer-questions", args=[task.pk]),
            {"answers": [{"id": question.pk, "answer": "python"}]},
            format="json",
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "task_not_awaiting_answers"

    def test_questions_endpoint_lists_the_batch(
        self, connected_auth_client: APIClient, repository: Any
    ) -> None:
        task = _paused_task(repository)
        _question(task, key="language")
        _question(task, key="styling")

        response = connected_auth_client.get(
            reverse("v1:agent-task-questions", args=[task.pk])
        )

        assert response.status_code == 200
        keys = {row["key"] for row in response.data}
        assert keys == {"language", "styling"}
        assert all(row["is_answered"] is False for row in response.data)

    def test_a_follow_up_message_is_refused_while_answers_are_pending(
        self, connected_auth_client: APIClient, repository: Any
    ) -> None:
        task = _paused_task(repository)
        _question(task)

        response = connected_auth_client.post(
            reverse("v1:agent-task-messages", args=[task.pk]),
            {"content": "Please hurry."},
            format="json",
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "awaiting_answers"

    def test_another_user_cannot_see_the_questions(
        self, connected_auth_client: APIClient, other_user: Any, repository: Any
    ) -> None:
        task = _paused_task(repository)
        _question(task)
        other = _client_for(other_user)

        response = other.get(reverse("v1:agent-task-questions", args=[task.pk]))

        assert response.status_code == 404


class TestClarificationModel:
    def test_answered_text_renders_the_decisions(self, repository: Any) -> None:
        task = _paused_task(repository)
        first = _question(task, key="language")
        first.answer_with("python")
        first.save(update_fields=["answer", "answered_at"])

        text = task.answered_clarifications_text()

        assert "Decisions already agreed" in text
        assert "python" in text

    def test_answered_text_is_empty_before_any_answer(self, repository: Any) -> None:
        task = _paused_task(repository)
        _question(task)

        assert task.answered_clarifications_text() == ""

    def test_pending_clarifications_returns_the_latest_round(self, repository: Any) -> None:
        task = _paused_task(repository)
        _question(task, key="language", round=1)
        _question(task, key="styling", round=2)

        pending = list(task.pending_clarifications)

        assert [row.key for row in pending] == ["styling"]

    def test_awaiting_answers_tracks_the_status(self, repository: Any) -> None:
        task = _paused_task(repository)

        assert task.awaiting_answers
        task.status = AgentTask.Status.QUEUED
        assert not task.awaiting_answers


class TestClarifyEvents:
    def test_announce_clarification_emits_a_renderable_payload(self, repository: Any) -> None:
        task = _paused_task(repository)
        question = _question(task, key="language")

        events.announce_clarification(task, question)

        event = RunEvent.objects.filter(task=task, kind=RunEvent.Kind.CLARIFICATION).latest("seq")
        assert event.payload["key"] == "language"
        assert event.payload["kind"] == "single_choice"
        assert len(event.payload["options"]) == 2


# ── helpers ──────────────────────────────────────────────────────────────────


def _system() -> Any:
    from langchain_core.messages import SystemMessage

    return SystemMessage(content="You are the clarify phase.")


def _human() -> Any:
    from langchain_core.messages import HumanMessage

    return HumanMessage(content="Make a login form.")


def _run(
    controller: ClarifyController,
    calls: list[tuple[str, dict[str, Any]]],
) -> Any:
    """Drive the controller directly, bypassing the graph (for skip-only cases).

    The graph itself is exercised by :class:`TestClarifyGraph`; this helper just
    applies tool calls in order and reports the resulting phase outcome.
    """
    for index, (name, arguments) in enumerate(calls):
        controller.run(ToolCall(id=f"call_{index}", name=name, arguments=arguments))
    from apps.agent.clarify import ClarifyResult

    return ClarifyResult(
        questions=controller.questions,
        skipped=controller.skipped,
    )


def _paused_task(repository: Any, **overrides: Any) -> AgentTask:
    """A task parked in ``clarifying`` with no questions yet."""
    defaults: dict[str, Any] = {
        "user": repository.user,
        "repository": repository,
        "prompt": "Make a login form.",
        "feature_branch": "codepilot/make-a-login-form",
        "status": AgentTask.Status.CLARIFYING,
        "phase": AgentTask.Phase.CLARIFY,
    }
    defaults.update(overrides)
    return AgentTask.objects.create(**defaults)


def _question(task: AgentTask, key: str = "language", round: int = 1) -> Clarification:
    return Clarification.objects.create(
        task=task,
        order_index=0,
        round=round,
        key=key,
        question=f"Which {key}?",
        kind=Clarification.Kind.SINGLE_CHOICE,
        options=[
            {"value": "python", "label": "Python"},
            {"value": "javascript", "label": "JavaScript"},
        ],
    )


def _client_for(user: Any) -> APIClient:
    from rest_framework_simplejwt.tokens import RefreshToken

    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client

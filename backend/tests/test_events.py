"""Tests for the run event stream, sequencing, and SSE framing."""

from __future__ import annotations

import json
from typing import Any

import pytest

from apps.agent_tasks.events import emit_event, set_phase, set_status
from apps.agent_tasks.models import AgentTask, RunEvent

pytestmark = pytest.mark.django_db


class TestSequenceNumbering:
    def test_first_event_is_sequence_one(self, task: AgentTask) -> None:
        assert emit_event(task, RunEvent.Kind.LOG, {"a": 1}).seq == 1

    def test_sequence_increments_monotonically(self, task: AgentTask) -> None:
        seqs = [emit_event(task, RunEvent.Kind.LOG).seq for _ in range(5)]

        assert seqs == [1, 2, 3, 4, 5]

    def test_sequences_are_scoped_per_task(
        self, task: AgentTask, repository: Any, connected_user: Any
    ) -> None:
        other = AgentTask.objects.create(
            user=connected_user,
            repository=repository,
            prompt="A second task",
            feature_branch="codepilot/second",
        )
        emit_event(task, RunEvent.Kind.LOG)
        emit_event(task, RunEvent.Kind.LOG)

        assert emit_event(other, RunEvent.Kind.LOG).seq == 1

    def test_events_are_ordered_oldest_first(self, task: AgentTask) -> None:
        for index in range(3):
            emit_event(task, RunEvent.Kind.LOG, {"index": index})

        assert [e.seq for e in task.events.order_by("seq")] == [1, 2, 3]
        assert [e.payload["index"] for e in task.events.order_by("seq")] == [0, 1, 2]

    def test_payload_defaults_to_an_empty_dict(self, task: AgentTask) -> None:
        assert emit_event(task, RunEvent.Kind.LOG).payload == {}


class TestPhaseTransitions:
    def test_set_phase_updates_and_announces(self, task: AgentTask) -> None:
        set_phase(task, AgentTask.Phase.EXPLORE)

        task.refresh_from_db()
        assert task.phase == AgentTask.Phase.EXPLORE
        assert task.started_at is not None

        event = task.events.filter(kind=RunEvent.Kind.PHASE).get()
        assert event.payload["phase"] == AgentTask.Phase.EXPLORE

    def test_phase_note_is_included_when_given(self, task: AgentTask) -> None:
        set_phase(task, AgentTask.Phase.PROPOSE, note="drafting login form")

        event = task.events.filter(kind=RunEvent.Kind.PHASE).get()
        assert event.payload["note"] == "drafting login form"

    def test_started_at_is_not_overwritten_on_later_phases(self, task: AgentTask) -> None:
        set_phase(task, AgentTask.Phase.EXPLORE)
        first_start = AgentTask.objects.get(pk=task.pk).started_at

        set_phase(task, AgentTask.Phase.PROPOSE)

        assert AgentTask.objects.get(pk=task.pk).started_at == first_start


class TestStatusTransitions:
    def test_terminal_status_stamps_finished_at_and_emits_done(self, task: AgentTask) -> None:
        set_status(task, AgentTask.Status.PUSHED)

        task.refresh_from_db()
        assert task.status == AgentTask.Status.PUSHED
        assert task.finished_at is not None
        assert task.events.filter(kind=RunEvent.Kind.DONE).exists()

    def test_non_terminal_status_leaves_finished_at_empty(self, task: AgentTask) -> None:
        set_status(task, AgentTask.Status.RUNNING)

        task.refresh_from_db()
        assert task.finished_at is None
        assert not task.events.filter(kind=RunEvent.Kind.DONE).exists()

    def test_error_code_and_message_are_persisted_and_announced(self, task: AgentTask) -> None:
        set_status(
            task,
            AgentTask.Status.FAILED,
            error_code="verification_failed",
            error_message="pytest failed with 3 errors",
        )

        task.refresh_from_db()
        assert task.error_code == "verification_failed"
        assert task.error_message == "pytest failed with 3 errors"

        event = task.events.filter(kind=RunEvent.Kind.STATUS).get()
        assert event.payload["error_code"] == "verification_failed"
        assert event.payload["error_message"] == "pytest failed with 3 errors"


class TestSseFraming:
    def test_frame_has_id_event_and_data_lines(self, task: AgentTask) -> None:
        event = emit_event(task, RunEvent.Kind.CHANGE_PROPOSED, {"path": "app/views.py"})

        frame = event.to_sse()

        lines = frame.split("\n")
        assert lines[0] == f"id: {event.seq}"
        assert lines[1] == "event: change_proposed"
        assert lines[2].startswith("data: ")
        assert frame.endswith("\n\n")

    def test_data_payload_round_trips_as_json(self, task: AgentTask) -> None:
        event = emit_event(task, RunEvent.Kind.PHASE, {"phase": "explore"})

        data_line = event.to_sse().split("data: ")[1].strip()
        parsed = json.loads(data_line)

        assert parsed == {
            "seq": event.seq,
            "kind": "phase",
            "payload": {"phase": "explore"},
        }

    def test_frames_for_consecutive_events_are_in_order(self, task: AgentTask) -> None:
        first = emit_event(task, RunEvent.Kind.LOG)
        second = emit_event(task, RunEvent.Kind.PHASE)

        assert first.to_sse().split("\n")[0] == "id: 1"
        assert second.to_sse().split("\n")[0] == "id: 2"

    def test_special_characters_in_payload_are_escaped(self, task: AgentTask) -> None:
        event = emit_event(task, RunEvent.Kind.LOG, {"message": 'line1\nline2 "quoted"'})

        data_line = event.to_sse().split("data: ")[1].strip()
        # Raw newlines would break the SSE frame, so they must be JSON-escaped.
        assert "\n" not in data_line
        assert json.loads(data_line)["payload"]["message"] == 'line1\nline2 "quoted"'

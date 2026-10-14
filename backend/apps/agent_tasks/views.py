"""API views for agent tasks: creation, approvals, SSE events, and messages.

This layer is thin by design -- the heavy lifting (LangGraph orchestration) runs
in Celery workers so that long agent runs never block an HTTP worker.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from django.conf import settings
from django.db import transaction
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.agent_tasks import events
from apps.agent_tasks.models import AgentTask, Message, ProposedChange, RunEvent
from apps.agent_tasks.serializers import (
    AgentTaskCreateSerializer,
    AgentTaskDetailSerializer,
    AgentTaskSummarySerializer,
    MessageCreateSerializer,
    MessageSerializer,
    ProposedChangeDecisionSerializer,
    ProposedChangeSerializer,
    RunEventSerializer,
)
from apps.agent_tasks.usage import assert_daily_budget
from apps.repositories.models import Branch, Repository
from config.observability import get_logger

logger = get_logger(__name__)

SSE_HEARTBEAT_SECONDS = 15
SSE_MAX_SECONDS = 60 * 30


class AgentTaskViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Create, inspect, and steer agent tasks."""

    permission_classes = (IsAuthenticated,)
    throttle_scope = "agent_task"
    filterset_fields = ("status", "phase", "repository")
    search_fields = ("title", "prompt", "feature_branch")
    ordering_fields = ("created_at", "updated_at")
    ordering = ("-created_at",)

    def get_queryset(self) -> Any:
        """Return only the requesting user's tasks."""
        return AgentTask.objects.filter(user=self.request.user).select_related("repository")

    def get_serializer_class(self) -> type:
        """Swap serializers between list and detail actions."""
        if self.action == "retrieve":
            return AgentTaskDetailSerializer
        if self.action == "create":
            return AgentTaskCreateSerializer
        return AgentTaskSummarySerializer

    @extend_schema(
        summary="List agent tasks",
        responses=AgentTaskSummarySerializer(many=True),
    )
    def list(self, request: Any, *args: Any, **kwargs: Any) -> Response:
        """Return a page of the user's tasks, newest first."""
        return super().list(request, *args, **kwargs)

    @extend_schema(
        summary="Start a task",
        request=AgentTaskCreateSerializer,
        responses={201: AgentTaskDetailSerializer},
    )
    def create(self, request: Any, *args: Any, **kwargs: Any) -> Response:
        """Create a task on a new feature branch and enqueue the agent run."""
        serializer = AgentTaskCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        repository = get_object_or_404(
            Repository.objects.filter(user=request.user),
            pk=data["repository_id"],
        )

        if not request.user.is_github_connected:
            return Response(
                {
                    "error": {
                        "code": "github_not_connected",
                        "message": "Connect your GitHub account first.",
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Checked before any row is written: a rejected task must not be left
        # queued with no worker, so the hard stop fires while the request is
        # still side-effect free.
        assert_daily_budget(request.user)

        with transaction.atomic():
            task = AgentTask.objects.create(
                user=request.user,
                repository=repository,
                base_branch=data.get("base_branch") or repository.default_branch,
                prompt=data["prompt"],
                title=data.get("title") or "",
                status=AgentTask.Status.QUEUED,
                phase=AgentTask.Phase.QUEUED,
            )
            feature_branch = self._allocate_branch_name(task)
            task.feature_branch = feature_branch
            task.save(update_fields=["feature_branch", "updated_at"])

            Branch.objects.get_or_create(
                repository=repository,
                name=feature_branch,
                defaults={"kind": Branch.Kind.FEATURE},
            )
            Repository.objects.filter(pk=repository.pk).update(task_count=repository.task_count + 1)

            events.emit_event(
                task,
                RunEvent.Kind.STATUS,
                {"status": task.status, "feature_branch": feature_branch},
            )

        self._enqueue(task)

        return Response(
            AgentTaskDetailSerializer(task).data,
            status=status.HTTP_201_CREATED,
        )

    @staticmethod
    def _allocate_branch_name(task: AgentTask) -> str:
        """Build a unique, readable feature branch name for the task.

        ``codepilot/<slug>`` is attempted first; a numeric suffix is appended if
        the branch already exists so repeated tasks never collide.
        """
        base = f"codepilot/{task.slug}"
        taken = set(
            Branch.objects.filter(repository=task.repository).values_list("name", flat=True)
        )
        if base not in taken:
            return base
        for suffix in range(2, 100):
            candidate = f"{base}-{suffix}"
            if candidate not in taken:
                return candidate
        raise ValueError("Could not allocate a unique branch name for this task.")

    @staticmethod
    def _enqueue(task: AgentTask) -> None:
        """Hand the run to a Celery worker.

        The task row is already committed in ``QUEUED`` state, so a broker
        outage loses nothing: the client can see the queued task and retry.
        The worker is :func:`apps.agent.tasks.run_agent_task`, which advances
        the task through the pipeline by its current status. When
        ``AGENT_PIPELINE_ENABLED`` is off -- e.g. the test settings -- a log
        event says so instead of dispatching a run that cannot succeed.

        The daily budget is re-checked here as well, so any future caller of
        this funnel is gated even if it did not check earlier.
        """
        assert_daily_budget(task.user)

        if not settings.AGENT_PIPELINE_ENABLED:
            events.emit_event(
                task,
                RunEvent.Kind.LOG,
                {
                    "message": (
                        "Task queued. The agent pipeline is not enabled on this server "
                        "yet, so no run has started."
                    )
                },
            )
            return

        try:
            from apps.agent.tasks import run_agent_task

            run_agent_task.delay(task.pk)
        except Exception:
            logger.exception("agent_task_enqueue_failed", task_id=task.pk)

    @extend_schema(summary="Task detail", responses=AgentTaskDetailSerializer)
    def retrieve(self, request: Any, *args: Any, **kwargs: Any) -> Response:
        """Return the full state of one task."""
        return super().retrieve(request, *args, **kwargs)

    @action(detail=True, methods=["get"], url_path="changes")
    @extend_schema(
        summary="Proposed changes",
        description="Every file change the agent proposed, with its unified diff.",
        responses=ProposedChangeSerializer(many=True),
    )
    def changes(self, request: Any, pk: int | None = None) -> Response:
        """Return the task's proposed changes in display order."""
        task = self.get_object()
        queryset = task.proposed_changes.order_by("order_index", "id")
        return Response(ProposedChangeSerializer(queryset, many=True).data)

    @action(detail=True, methods=["post"], url_path=r"changes/(?P<change_id>\d+)/decide")
    @extend_schema(
        summary="Decide on a proposed change",
        request=ProposedChangeDecisionSerializer,
        responses={200: ProposedChangeSerializer},
    )
    def decide(self, request: Any, pk: int | None = None, change_id: int | None = None) -> Response:
        """Approve, reject, or request an edit for one proposed change."""
        serializer = ProposedChangeDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        decision = serializer.validated_data["decision"]
        feedback = serializer.validated_data.get("feedback", "").strip()

        task = self.get_object()
        change = get_object_or_404(task.proposed_changes, pk=change_id)

        with transaction.atomic():
            if decision == "approve":
                change.approve(actor=request.user)
            elif decision == "reject":
                change.reject(actor=request.user)
            else:
                # Requesting an edit keeps the change pending with instructions.
                change.user_feedback = feedback
            change.save()

            events.emit_event(
                task,
                RunEvent.Kind.CHANGE_DECIDED,
                {
                    "change_id": change.pk,
                    "decision": decision,
                    "path": change.path,
                    "actor_id": request.user.pk,
                    "actor_username": request.user.get_username(),
                    "decided_at": change.decided_at.isoformat() if change.decided_at else None,
                },
            )

        return Response(ProposedChangeSerializer(change).data)

    @action(detail=True, methods=["post"], url_path="execute")
    @extend_schema(
        summary="Start applying approved changes",
        description=(
            "Kick off apply/verify/commit/push once every change has been "
            "decided and at least one is approved. Rejected changes are "
            "skipped; changes still awaiting a decision block the run."
        ),
        responses={200: AgentTaskDetailSerializer},
    )
    def execute(self, request: Any, pk: int | None = None) -> Response:
        """Start the execution stage after the user decided every change."""
        task = self.get_object()
        if not task.awaiting_decision:
            return Response(
                {
                    "error": {
                        "code": "task_not_awaiting_approval",
                        "message": (
                            "The task is not waiting for approval decisions, so "
                            "there is nothing to execute."
                        ),
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )

        pending = task.proposed_changes.filter(status=ProposedChange.Status.PENDING).count()
        approved = task.proposed_changes.filter(status=ProposedChange.Status.APPROVED).count()
        if pending:
            return Response(
                {
                    "error": {
                        "code": "changes_pending_decision",
                        "message": f"{pending} change(s) still await your decision.",
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )
        if not approved:
            return Response(
                {
                    "error": {
                        "code": "no_approved_changes",
                        "message": "Approve at least one change before starting.",
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )

        self._enqueue(task)
        return Response(AgentTaskDetailSerializer(task).data)

    @action(detail=True, methods=["get", "post"], url_path="messages")
    @extend_schema(
        summary="Task conversation",
        request=MessageCreateSerializer,
        responses={200: MessageSerializer(many=True), 201: MessageSerializer},
    )
    def messages(self, request: Any, pk: int | None = None) -> Response:
        """Read the thread, or post a follow-up instruction on the same branch."""
        task = self.get_object()

        if request.method == "GET":
            queryset = task.messages.order_by("created_at", "id")
            return Response(MessageSerializer(queryset, many=True).data)

        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if task.is_busy:
            return Response(
                {
                    "error": {
                        "code": "task_busy",
                        "message": "Wait for the current run to finish before sending a follow-up.",
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Gate before recording anything: a follow-up starts a fresh run, so the
        # same daily cap applies and the message must not be left un-run.
        assert_daily_budget(task.user)

        content = serializer.validated_data["content"]
        with transaction.atomic():
            Message.objects.create(task=task, role=Message.Role.USER, content=content)
            task.followup_count += 1
            task.save(update_fields=["followup_count", "updated_at"])
            events.emit_event(task, RunEvent.Kind.MESSAGE, {"role": "user", "content": content})
            events.set_status(task, AgentTask.Status.QUEUED)

        # A follow-up is a new run on the same branch, seeded with the thread's
        # prior context: dispatch it exactly like task creation does.
        self._enqueue(task)

        return Response(
            MessageSerializer(task.messages.order_by("created_at").last()).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="cancel")
    @extend_schema(
        summary="Cancel a task",
        request=None,
        responses={200: AgentTaskDetailSerializer},
    )
    def cancel(self, request: Any, pk: int | None = None) -> Response:
        """Stop a queued or running task. Terminal tasks are left untouched."""
        task = self.get_object()
        if task.is_terminal:
            return Response(AgentTaskDetailSerializer(task).data)

        events.set_status(
            task,
            AgentTask.Status.CANCELLED,
            error_code="cancelled",
            error_message="Cancelled by the user.",
        )
        return Response(AgentTaskDetailSerializer(task).data)

    @action(detail=True, methods=["get"], url_path="events")
    @extend_schema(
        summary="Replay the event history",
        parameters=[
            OpenApiParameter(
                "after_seq",
                int,
                location=OpenApiParameter.QUERY,
                description="Return only events with a sequence number greater than this.",
            )
        ],
        responses=RunEventSerializer(many=True),
    )
    def events_history(self, request: Any, pk: int | None = None) -> Response:
        """Return the task's events as plain JSON, for clients that cannot stream."""
        task = self.get_object()
        after_seq = int(request.query_params.get("after_seq", 0))
        queryset = task.events.filter(seq__gt=after_seq).order_by("seq")[:500]
        return Response(RunEventSerializer(queryset, many=True).data)


class AgentTaskEventStreamView(APIView):
    """Server-Sent Events stream of a task's run.

    Reconnect with ``?after_seq=N`` to resume exactly where the client left off;
    the event table is the durable buffer, so a killed app loses nothing.
    """

    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="Stream task events (SSE)",
        parameters=[
            OpenApiParameter(
                "after_seq",
                int,
                location=OpenApiParameter.QUERY,
                description="Replay events after this sequence number before streaming.",
            )
        ],
        responses={200: None},
    )
    def get(self, request: Any, task_id: int) -> StreamingHttpResponse:
        """Return an ``text/event-stream`` response for one task."""
        task = get_object_or_404(
            AgentTask.objects.filter(user=request.user),
            pk=task_id,
        )
        after_seq = int(request.query_params.get("after_seq", 0))
        response = StreamingHttpResponse(
            self._event_stream(task, after_seq),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        response["Connection"] = "keep-alive"
        return response

    def _event_stream(self, task: AgentTask, after_seq: int) -> Iterator[str]:
        """Yield SSE frames, polling the event table and heartbeating when idle.

        Polling the database keeps this deployable on plain WSGI; a future move to
        Redis pub/sub only replaces the inner loop.
        """
        last_seq = after_seq
        started = time.monotonic()
        last_beat = started

        while True:
            batch = list(RunEvent.objects.filter(task=task, seq__gt=last_seq).order_by("seq")[:200])
            for event in batch:
                last_seq = event.seq
                yield event.to_sse()

            if batch and batch[-1].kind == RunEvent.Kind.DONE:
                # Tell the client to stop, then close so the socket is released.
                yield "event: close\ndata: {}\n\n"
                return

            now = time.monotonic()
            if now - last_beat >= SSE_HEARTBEAT_SECONDS:
                last_beat = now
                yield ": keep-alive\n\n"
            if now - started >= SSE_MAX_SECONDS:
                yield "event: timeout\ndata: {}\n\n"
                return

            time.sleep(0.5)

            # Refresh the terminal flag so a cancel request ends the stream.
            task.refresh_from_db(fields=("status",))
            if task.status == AgentTask.Status.CANCELLED:
                yield "event: close\ndata: {}\n\n"
                return


__all__: list[str] = ["AgentTaskEventStreamView", "AgentTaskViewSet"]

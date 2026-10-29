"""Agent tasks, proposed changes, run events, and thread messages.

The Django database -- not a LangGraph checkpointer -- is the source of truth for
task state. An agent run always executes to a terminal point (proposal, push, or
failure) and then hands control back to the mobile client. Resuming after the user
approves starts a *new* run seeded with the thread's prior context. This keeps
agent state fully inspectable, transactionally consistent, and resumable across
Celery processes.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, ClassVar

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.agent_tasks.pricing import cost_usd


class AgentTask(models.Model):
    """One natural-language task the user asked the agent to perform."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        QUEUED = "queued", "Queued"
        CLARIFYING = "clarifying", "Needs your answers"
        RUNNING = "running", "Running"
        AWAITING_APPROVAL = "awaiting_approval", "Awaiting approval"
        APPLYING = "applying", "Applying changes"
        VERIFYING = "verifying", "Running checks"
        PUSHED = "pushed", "Pushed"
        MERGED = "merged", "Merged"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    class Phase(models.TextChoices):
        QUEUED = "queued", "Queued"
        PLAN = "plan", "Planning"
        CLARIFY = "clarify", "Asking you a few questions"
        EXPLORE = "explore", "Exploring repository"
        PROPOSE = "propose", "Drafting changes"
        AWAIT_APPROVAL = "await_approval", "Waiting for your approval"
        APPLY = "apply", "Applying changes"
        VERIFY = "verify", "Running formatter and tests"
        REPAIR = "repair", "Repairing failures"
        COMMIT = "commit", "Committing"
        PUSH = "push", "Pushing and opening PR"
        DONE = "done", "Done"

    #: Statuses that will never change again.
    TERMINAL_STATUSES: ClassVar[frozenset[str]] = frozenset(
        {Status.PUSHED, Status.MERGED, Status.FAILED, Status.CANCELLED}
    )
    #: Statuses for which a client poll/stream should not block.
    IN_PROGRESS_STATUSES: ClassVar[frozenset[str]] = frozenset(
        {
            Status.QUEUED,
            Status.CLARIFYING,
            Status.RUNNING,
            Status.APPLYING,
            Status.VERIFYING,
            Status.AWAITING_APPROVAL,
        }
    )
    #: Statuses where a worker actually owns the task right now. A follow-up is
    #: refused in these states because the running agent would never see it.
    #: ``QUEUED`` is deliberately excluded: nothing is executing yet, so a
    #: follow-up can still be folded into the next run.
    BUSY_STATUSES: ClassVar[frozenset[str]] = frozenset(
        {
            Status.RUNNING,
            Status.APPLYING,
            Status.VERIFYING,
            Status.AWAITING_APPROVAL,
        }
    )

    # ── Ownership ───────────────────────────────────────────────────────────
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agent_tasks",
    )
    repository = models.ForeignKey(
        "repositories.Repository",
        on_delete=models.CASCADE,
        related_name="tasks",
    )

    # ── Git coordinates ─────────────────────────────────────────────────────
    base_branch = models.CharField(
        max_length=255,
        default="main",
        help_text="Branch the feature branch was cut from.",
    )
    feature_branch = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Agent-created working branch. One per task.",
    )
    base_sha = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="SHA of base_branch when the task started, for conflict detection.",
    )

    # ── Task content ────────────────────────────────────────────────────────
    title = models.CharField(max_length=255, blank=True, default="")
    prompt = models.TextField(help_text="The user's natural-language request.")
    followup_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of follow-up messages sent on this branch.",
    )

    # ── State ───────────────────────────────────────────────────────────────
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    phase = models.CharField(
        max_length=32,
        choices=Phase.choices,
        default=Phase.QUEUED,
    )
    current_run_id = models.UUIDField(null=True, blank=True)

    # ── Agent output ────────────────────────────────────────────────────────
    agent_summary = models.TextField(
        blank=True,
        default="",
        help_text="Plain-language summary of what was done, shown on the result screen.",
    )
    agent_reasoning = models.TextField(
        blank=True,
        default="",
        help_text="Collapsed reasoning trace from the model.",
    )
    detected_stack = models.JSONField(
        default=dict,
        blank=True,
        help_text="Auto-detected language/toolchain, e.g. {'python': '3.12', 'pytest': True}.",
    )

    # ── Result ──────────────────────────────────────────────────────────────
    commit_sha = models.CharField(max_length=64, blank=True, default="")
    commit_message = models.TextField(blank=True, default="")
    pr_number = models.PositiveIntegerField(null=True, blank=True)
    pr_url = models.URLField(max_length=500, blank=True, default="")
    diff_stat = models.JSONField(default=dict, blank=True)
    verification = models.JSONField(
        default=dict,
        blank=True,
        help_text="Formatter/linter/test outcomes for this task.",
    )

    # ── Cost & limits ───────────────────────────────────────────────────────
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    reasoning_tokens = models.PositiveIntegerField(default=0)
    cached_tokens = models.PositiveIntegerField(
        default=0,
        help_text="Input tokens served from Groq's prompt cache (billed at half rate).",
    )
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    llm_calls = models.PositiveIntegerField(default=0)
    tool_calls = models.PositiveIntegerField(default=0)

    # ── Errors & timing ─────────────────────────────────────────────────────
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "agent_tasks_task"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("user", "-created_at"), name="task_user_created_idx"),
            models.Index(fields=("repository", "-created_at"), name="task_repo_created_idx"),
            models.Index(fields=("user", "status"), name="task_user_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("repository", "feature_branch"),
                condition=~models.Q(feature_branch=""),
                name="uniq_task_per_feature_branch",
            )
        ]
        verbose_name = "agent task"
        verbose_name_plural = "agent tasks"

    def __str__(self) -> str:
        return f"#{self.pk} {self.title or self.prompt[:60]}"

    # ── Derived state ───────────────────────────────────────────────────────
    @property
    def is_terminal(self) -> bool:
        """True when the task will not change state without user action."""
        return self.status in self.TERMINAL_STATUSES

    @property
    def is_in_progress(self) -> bool:
        """True while a client should keep watching for progress."""
        return self.status in self.IN_PROGRESS_STATUSES

    @property
    def is_busy(self) -> bool:
        """True while a worker owns this task and a follow-up would be lost."""
        return self.status in self.BUSY_STATUSES

    @property
    def awaiting_decision(self) -> bool:
        """True when the task is blocked on the user's approve/reject decision."""
        return self.status == self.Status.AWAITING_APPROVAL

    @property
    def awaiting_answers(self) -> bool:
        """True when the task is paused waiting for the user's clarifying answers."""
        return self.status == self.Status.CLARIFYING

    @property
    def total_tokens(self) -> int:
        """Total tokens consumed by this task."""
        return self.input_tokens + self.output_tokens + self.reasoning_tokens

    @property
    def branch_name(self) -> str:
        """Feature branch name, derived if not yet assigned."""
        if self.feature_branch:
            return self.feature_branch
        return f"codepilot/{self.slug}"

    @property
    def slug(self) -> str:
        """URL-safe short identifier derived from the title or prompt."""
        source = self.title or self.prompt
        cleaned = re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-")
        return (cleaned[:40] or "task").strip("-")

    @property
    def duration_seconds(self) -> float | None:
        """Wall-clock duration, or ``None`` if the task has not finished."""
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def pending_clarifications(self) -> Any:
        """The outstanding questions for the latest round, if any."""
        latest = self.clarifications.order_by("-round").values_list("round", flat=True).first()
        if latest is None:
            return self.clarifications.none()
        return self.clarifications.filter(round=latest)

    def answered_clarifications_text(self) -> str:
        """Render every answered question as constraints for the agent.

        Appended to the task prompt so the agent honours the user's choices
        (language, styling, auth flow, …) instead of guessing, and so a later
        round builds on the answers already given.
        """
        rows = [
            row
            for row in self.clarifications.order_by("round", "order_index", "id")
            if row.is_answered
        ]
        if not rows:
            return ""
        lines = ["## Decisions already agreed with the user"]
        lines += [f"- {row.question.strip()} **{row.answer}**" for row in rows]
        return "\n".join(lines)

    def mark_finished(self, *, status: str | None = None) -> None:
        """Stamp ``finished_at`` and settle the status."""
        self.finished_at = timezone.now()
        if status:
            self.status = status
        if self.status in self.TERMINAL_STATUSES and self.phase == self.Phase.QUEUED:
            self.phase = self.Phase.DONE

    def record_usage(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
        cached_tokens: int = 0,
    ) -> None:
        """Accumulate LLM usage across every call in this task and price it.

        Cost is derived from the same tokens through
        :func:`apps.agent_tasks.pricing.cost_usd`, so the stored column can
        never drift from the locked rates -- including the prompt-cache
        discount on ``cached_tokens``.
        """
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.reasoning_tokens += reasoning_tokens
        self.cached_tokens += cached_tokens
        self.cost_usd = (self.cost_usd or Decimal("0")) + cost_usd(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cached_tokens=cached_tokens,
        )

    def to_dict(self) -> dict[str, Any]:
        """Compact representation used for logging and debugging."""
        return {
            "id": self.pk,
            "status": self.status,
            "phase": self.phase,
            "repository": self.repository.full_name,
            "feature_branch": self.feature_branch,
            "total_tokens": self.total_tokens,
        }


class ProposedChange(models.Model):
    """One file-level change the agent proposes, awaiting the user's decision."""

    class ChangeType(models.TextChoices):
        CREATE = "create", "Create file"
        MODIFY = "modify", "Modify file"
        DELETE = "delete", "Delete file"
        RENAME = "rename", "Rename file"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        APPLIED = "applied", "Applied"
        FAILED = "failed", "Failed to apply"
        SKIPPED = "skipped", "Skipped"

    task = models.ForeignKey(
        AgentTask,
        on_delete=models.CASCADE,
        related_name="proposed_changes",
    )
    order_index = models.PositiveIntegerField(default=0)

    path = models.CharField(max_length=1000, help_text="Repo-relative file path.")
    change_type = models.CharField(max_length=16, choices=ChangeType.choices)
    previous_path = models.CharField(
        max_length=1000,
        blank=True,
        default="",
        help_text="Original path for a rename.",
    )
    patch = models.TextField(
        help_text="Unified diff for this change. The model never emits whole files.",
    )
    rationale = models.TextField(
        blank=True,
        default="",
        help_text="One-line explanation of why this change is needed.",
    )
    additions = models.PositiveIntegerField(default=0)
    deletions = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    user_feedback = models.TextField(
        blank=True,
        default="",
        help_text="User's 'request edit' instructions, fed back to the agent.",
    )
    error_message = models.TextField(blank=True, default="")

    #: Audit actor: who approved/rejected this change. Kept as a nullable FK so
    #: the timestamped decision survives even if the account is later removed.
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="decided_changes",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "agent_tasks_proposed_change"
        ordering = ("order_index", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("task", "path"),
                name="uniq_change_per_task_path",
            )
        ]
        indexes = [
            models.Index(fields=("task", "status"), name="change_task_status_idx"),
        ]
        verbose_name = "proposed change"
        verbose_name_plural = "proposed changes"

    def __str__(self) -> str:
        return f"{self.task_id}:{self.path}"

    @property
    def is_decided(self) -> bool:
        """True once the user has approved or rejected this change."""
        return self.status in {self.Status.APPROVED, self.Status.REJECTED, self.Status.APPLIED}

    def approve(self, actor: Any = None) -> None:
        """Mark this change as approved, recording who approved it."""
        self.status = self.Status.APPROVED
        self.decided_at = timezone.now()
        if actor is not None:
            self.decided_by = actor

    def reject(self, actor: Any = None) -> None:
        """Mark this change as rejected, recording who rejected it."""
        self.status = self.Status.REJECTED
        self.decided_at = timezone.now()
        if actor is not None:
            self.decided_by = actor


class Clarification(models.Model):
    """One question the agent asks before writing code.

    The agent is a senior engineer, not a code vending machine: for an
    underspecified ask ("make a login form") the useful move is to pin down
    language, styling, auth flow, and similar choices *before* proposing diffs.
    A row here is one such question, its offered options, and (once the user
    answers) the chosen value.

    Questions are grouped by ``round`` so the UI can show a single batch; the
    runner re-asks with a fresh round if an answer is still ambiguous.
    """

    class Kind(models.TextChoices):
        SINGLE_CHOICE = "single_choice", "Pick one"
        MULTI_CHOICE = "multi_choice", "Pick any"
        TEXT = "text", "Free text"

    task = models.ForeignKey(
        AgentTask,
        on_delete=models.CASCADE,
        related_name="clarifications",
    )
    order_index = models.PositiveIntegerField(default=0)

    #: Batch number, 1-based. Answers from round N are folded into the prompt
    #: the agent sees in round N+1, so a second round is always better informed.
    round = models.PositiveIntegerField(default=1)

    key = models.SlugField(
        max_length=64,
        help_text="Stable machine name, e.g. 'language'. Referenced in the prompt.",
    )
    question = models.TextField(help_text="The question, phrased for a human.")
    kind = models.CharField(
        max_length=16,
        choices=Kind.choices,
        default=Kind.SINGLE_CHOICE,
    )
    options = models.JSONField(
        default=list,
        blank=True,
        help_text="Choices for a choice question: [{'value': ..., 'label': ...}].",
    )
    allow_custom = models.BooleanField(
        default=True,
        help_text="Whether the user may answer with something not in options.",
    )
    rationale = models.TextField(
        blank=True,
        default="",
        help_text="Why the agent is asking; shown as supporting text.",
    )

    answer = models.TextField(
        blank=True,
        default="",
        help_text="The user's answer. Empty while the question is outstanding.",
    )
    answered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agent_tasks_clarification"
        ordering = ("round", "order_index", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("task", "round", "key"),
                name="uniq_clarification_per_task_round_key",
            )
        ]
        indexes = [
            models.Index(fields=("task", "round"), name="clarify_task_round_idx"),
        ]
        verbose_name = "clarification question"
        verbose_name_plural = "clarification questions"

    def __str__(self) -> str:
        return f"{self.task_id}#r{self.round} {self.key}"

    @property
    def is_answered(self) -> bool:
        """True once the user has supplied a non-empty answer."""
        return bool(self.answer.strip())

    def answer_with(self, value: str) -> None:
        """Record the user's answer and stamp when it arrived."""
        self.answer = value.strip()
        self.answered_at = timezone.now()


class RunEvent(models.Model):
    """An append-only event stream for one task; the source of the SSE feed.

    The client can always reconstruct the full run from these rows, which makes
    the stream resumable after the app is killed.
    """

    class Kind(models.TextChoices):
        STATUS = "status", "Status changed"
        PHASE = "phase", "Phase changed"
        LOG = "log", "Log line"
        REASONING = "reasoning", "Reasoning trace"
        CHANGE_PROPOSED = "change_proposed", "Change proposed"
        CHANGE_DECIDED = "change_decided", "Change decided"
        CLARIFICATION = "clarification", "Question asked"
        ANSWERED = "answered", "Question answered"
        VERIFICATION = "verification", "Verification result"
        PUSHED = "pushed", "Pushed to GitHub"
        MESSAGE = "message", "Chat message"
        ERROR = "error", "Error"
        DONE = "done", "Finished"

    task = models.ForeignKey(
        AgentTask,
        on_delete=models.CASCADE,
        related_name="events",
    )
    seq = models.PositiveIntegerField(
        help_text="Monotonic per-task sequence number, so clients can detect gaps.",
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "agent_tasks_run_event"
        ordering = ("task_id", "seq")
        constraints = [
            models.UniqueConstraint(fields=("task", "seq"), name="uniq_event_seq_per_task"),
        ]
        indexes = [
            models.Index(fields=("task", "seq"), name="event_task_seq_idx"),
        ]
        verbose_name = "run event"
        verbose_name_plural = "run events"

    def __str__(self) -> str:
        return f"{self.task_id}#{self.seq} {self.kind}"

    @classmethod
    def next_seq(cls, task: AgentTask) -> int:
        """Return the next sequence number for ``task``.

        Locks the parent task row so concurrent emitters (Celery worker plus an
        HTTP request) cannot claim the same ``seq`` and violate the unique
        constraint.
        """
        with transaction.atomic():
            locked = AgentTask.objects.select_for_update().get(pk=task.pk)
            last = (
                cls.objects.filter(task_id=locked.pk)
                .order_by("-seq")
                .values_list("seq", flat=True)
                .first()
            )
            return (last or 0) + 1

    def to_sse(self) -> str:
        """Serialise this event as one Server-Sent Events frame."""
        import json

        data = json.dumps(
            {"seq": self.seq, "kind": self.kind, "payload": self.payload},
            separators=(",", ":"),
        )
        return f"id: {self.seq}\nevent: {self.kind}\ndata: {data}\n\n"


class Message(models.Model):
    """A message in the task's conversational thread.

    Gives the agent memory across follow-ups so "actually use email, not username"
    works on the same branch.
    """

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        SYSTEM = "system", "System"

    task = models.ForeignKey(
        AgentTask,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agent_tasks_message"
        ordering = ("created_at", "id")
        indexes = [
            models.Index(fields=("task", "created_at"), name="msg_task_created_idx"),
        ]
        verbose_name = "message"
        verbose_name_plural = "messages"

    def __str__(self) -> str:
        return f"{self.task_id} {self.role}: {self.content[:40]}"

    @classmethod
    def recent_for_prompt(cls, task: AgentTask, limit: int = 12) -> list[Message]:
        """Return the most recent messages, oldest-first, for LLM context."""
        messages: list[Message] = list(
            cls.objects.filter(task=task).order_by("-created_at")[:limit]
        )
        messages.reverse()
        return messages

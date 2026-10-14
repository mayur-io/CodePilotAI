"""Serializers for the agent tasks API."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.agent_tasks.models import AgentTask, Message, ProposedChange, RunEvent


class ProposedChangeSerializer(serializers.ModelSerializer[ProposedChange]):
    """A single proposed file change, including its unified diff."""

    is_decided = serializers.BooleanField(read_only=True)
    decided_by_username = serializers.CharField(
        source="decided_by.username",
        read_only=True,
        default=None,
    )

    class Meta:
        model = ProposedChange
        fields = (
            "id",
            "task",
            "order_index",
            "path",
            "previous_path",
            "change_type",
            "patch",
            "rationale",
            "additions",
            "deletions",
            "status",
            "user_feedback",
            "error_message",
            "is_decided",
            "decided_by",
            "decided_by_username",
            "decided_at",
            "created_at",
        )
        read_only_fields = (
            "id",
            "task",
            "order_index",
            "patch",
            "additions",
            "deletions",
            "status",
            "error_message",
            "is_decided",
            "decided_by",
            "decided_by_username",
            "decided_at",
            "created_at",
        )


class ProposedChangeDecisionSerializer(serializers.Serializer[dict[str, Any]]):
    """Request body for approving or rejecting one change.

    ``request_edit`` keeps the change pending and feeds ``feedback`` back to the
    agent, which is how "no, use email not username" works.
    """

    decision = serializers.ChoiceField(choices=("approve", "reject", "request_edit"))
    feedback = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=4000,
        default="",
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Require feedback text whenever an edit is requested."""
        if attrs["decision"] == "request_edit" and not attrs.get("feedback", "").strip():
            raise serializers.ValidationError(
                {"feedback": "Describe what should change so the agent can revise this."}
            )
        return attrs


class AgentTaskSummarySerializer(serializers.ModelSerializer[AgentTask]):
    """Compact task representation for list screens."""

    repository_full_name = serializers.CharField(source="repository.full_name", read_only=True)
    awaiting_decision = serializers.BooleanField(read_only=True)
    is_terminal = serializers.BooleanField(read_only=True)
    total_tokens = serializers.IntegerField(read_only=True)

    class Meta:
        model = AgentTask
        fields: tuple[str, ...] = (
            "id",
            "title",
            "prompt",
            "status",
            "phase",
            "repository",
            "repository_full_name",
            "base_branch",
            "feature_branch",
            "awaiting_decision",
            "is_terminal",
            "total_tokens",
            "cached_tokens",
            "cost_usd",
            "pr_url",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class AgentTaskDetailSerializer(AgentTaskSummarySerializer):
    """Full task state, including agent output and verification results."""

    class Meta(AgentTaskSummarySerializer.Meta):
        fields: tuple[str, ...] = (
            *AgentTaskSummarySerializer.Meta.fields,
            "agent_summary",
            "agent_reasoning",
            "detected_stack",
            "diff_stat",
            "verification",
            "commit_sha",
            "commit_message",
            "pr_number",
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "llm_calls",
            "tool_calls",
            "error_code",
            "error_message",
            "started_at",
            "finished_at",
            "duration_seconds",
        )
        read_only_fields = fields


class AgentTaskCreateSerializer(serializers.Serializer[dict[str, Any]]):
    """Request body for starting a new task."""

    repository_id = serializers.IntegerField(min_value=1)
    prompt = serializers.CharField(min_length=3, max_length=8000, trim_whitespace=True)
    base_branch = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        default="",
        help_text="Defaults to the repository's default branch.",
    )
    title = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        default="",
        help_text="Optional short label. Derived from the prompt when omitted.",
    )


class MessageSerializer(serializers.ModelSerializer[Message]):
    """One turn in the task's conversational thread."""

    class Meta:
        model = Message
        fields = ("id", "role", "content", "created_at")
        read_only_fields = fields


class MessageCreateSerializer(serializers.Serializer[dict[str, Any]]):
    """Request body for a follow-up message on an existing branch."""

    content = serializers.CharField(min_length=1, max_length=8000, trim_whitespace=True)


class RunEventSerializer(serializers.ModelSerializer[RunEvent]):
    """A single entry in the task's event stream."""

    class Meta:
        model = RunEvent
        fields: tuple[str, ...] = ("seq", "kind", "payload", "created_at")
        read_only_fields = fields


__all__: list[str] = [
    "AgentTaskCreateSerializer",
    "AgentTaskDetailSerializer",
    "AgentTaskSummarySerializer",
    "MessageCreateSerializer",
    "MessageSerializer",
    "ProposedChangeDecisionSerializer",
    "ProposedChangeSerializer",
    "RunEventSerializer",
]

"""End-to-end tests for the pipeline runner's two stages.

Planning drives a task from ``queued`` to ``awaiting_approval`` with persisted
proposals. Execution drives an approved task through apply -> verify -> repair ->
commit -> push -> PR, ending ``pushed``, and every failure path lands in a
terminal FAILED state with an explicit error code. All model traffic is
scripted; the only network is an httpx call inside the PR step, which is
monkeypatched.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.agent.runner import run_execution_stage, run_planning_stage
from apps.agent.verify import CommandOutcome, VerificationResult
from apps.agent_tasks.models import AgentTask, Message, ProposedChange, RunEvent
from apps.repositories.models import Branch

pytestmark = pytest.mark.django_db

# app.py in the seeded origin is exactly:
#   def main():
#       return 1
MODIFY_APP_PY = """\
--- a/app.py
+++ b/app.py
@@ -1,2 +1,5 @@
 def main():
     return 1
+
+def helper():
+    return "hi"
"""

# Same file, applied *after* MODIFY_APP_PY so the context matches.
REPAIR_APP_PY = """\
--- a/app.py
+++ b/app.py
@@ -4,2 +4,2 @@
 def helper():
-    return "hi"
+    return "hello"
"""

CONFLICTING_PATCH = """\
--- a/app.py
+++ b/app.py
@@ -99,2 +99,3 @@
 def not_there():
     return 1
+    return 2
"""


def approve_change(task: AgentTask, patch: str, *, path: str = "app.py") -> ProposedChange:
    """Create an APPROVED change, the state the execution stage is gated on."""
    return ProposedChange.objects.create(
        task=task,
        order_index=0,
        path=path,
        change_type=ProposedChange.ChangeType.MODIFY,
        patch=patch,
        rationale="Approved in test.",
        status=ProposedChange.Status.APPROVED,
    )


def events_of(task: AgentTask, kind: RunEvent.Kind) -> list[RunEvent]:
    return list(task.events.filter(kind=kind).order_by("seq"))


def fake_pull_request(_self: Any, full_name: str, **kwargs: Any) -> dict[str, Any]:
    """Return a GitHub API-shaped payload for the monkeypatched PR call."""
    return {
        "number": 7,
        "html_url": f"https://github.com/{full_name}/pull/7",
        "title": kwargs.get("title", ""),
        "body": kwargs.get("body", ""),
    }


class TestPlanningStage:
    def test_planning_reaches_awaiting_approval(
        self,
        git_workspace: Any,
        task: AgentTask,
        scripted_provider: Any,
        tool_completion: Any,
        answer_completion: Any,
    ) -> None:
        provider = scripted_provider(
            [
                answer_completion(
                    "I understand the codebase.",
                    reasoning="The repo is small; app.py holds main.",
                ),
                tool_completion(
                    "propose_change",
                    {
                        "path": "app.py",
                        "change_type": "modify",
                        "patch": MODIFY_APP_PY,
                        "rationale": "Add a helper next to main.",
                    },
                ),
                tool_completion("finish", {"summary": "Added a helper function."}),
            ]
        )

        run_planning_stage(task, provider=provider)

        task.refresh_from_db()
        assert task.status == AgentTask.Status.AWAITING_APPROVAL
        assert task.phase == AgentTask.Phase.AWAIT_APPROVAL
        assert task.agent_summary == "Added a helper function."
        # Model reasoning from both phases is collapsed onto the task row.
        assert "repo is small" in task.agent_reasoning
        assert task.detected_stack == {}  # the seed repo has no toolchain

        change = task.proposed_changes.get()
        assert change.path == "app.py"
        assert change.status == ProposedChange.Status.PENDING
        assert (change.additions, change.deletions) == (3, 0)

        # The run's conversation is on record, assistant + tool turns.
        assert task.messages.filter(role=Message.Role.ASSISTANT).count() >= 3
        assert task.messages.filter(metadata__kind="tool").count() >= 2
        # Events streamed: status transitions, phases, and the proposal itself.
        assert len(events_of(task, RunEvent.Kind.CHANGE_PROPOSED)) == 1
        assert len(events_of(task, RunEvent.Kind.PHASE)) >= 4
        assert len(events_of(task, RunEvent.Kind.STATUS)) >= 2

    def test_planning_with_no_proposals_fails_the_task(
        self,
        git_workspace: Any,
        task: AgentTask,
        scripted_provider: Any,
        answer_completion: Any,
    ) -> None:
        provider = scripted_provider(
            [
                answer_completion("I understand the codebase."),
                answer_completion("There is nothing to change."),
            ]
        )

        run_planning_stage(task, provider=provider)

        task.refresh_from_db()
        assert task.status == AgentTask.Status.FAILED
        assert task.error_code == "no_changes_proposed"
        # The failure happens at the end of propose; phase is not rewound.
        assert task.phase == AgentTask.Phase.PROPOSE

    def test_provider_errors_are_mapped_to_a_terminal_state(
        self,
        git_workspace: Any,
        task: AgentTask,
        scripted_provider: Any,
    ) -> None:
        provider = scripted_provider([])  # no scripted completions at all

        run_planning_stage(task, provider=provider)

        task.refresh_from_db()
        assert task.status == AgentTask.Status.FAILED
        assert task.error_code == "provider_error"

    def test_planning_reuses_follow_up_instructions(
        self,
        git_workspace: Any,
        task: AgentTask,
        scripted_provider: Any,
        tool_completion: Any,
        answer_completion: Any,
    ) -> None:
        Message.objects.create(
            task=task,
            role=Message.Role.USER,
            content="Also keep Python 3.11 compatibility.",
        )
        provider = scripted_provider(
            [
                answer_completion("Read the follow-up."),
                tool_completion(
                    "propose_change",
                    {
                        "path": "app.py",
                        "change_type": "modify",
                        "patch": MODIFY_APP_PY,
                        "rationale": "Add a helper.",
                    },
                ),
                tool_completion("finish", {"summary": "Added a helper function."}),
            ]
        )

        run_planning_stage(task, provider=provider)

        joined = "\n".join(str(message.content) for message in provider.calls[1])
        assert "Python 3.11" in joined
        task.refresh_from_db()
        assert task.status == AgentTask.Status.AWAITING_APPROVAL


class TestExecutionStage:
    def test_happy_path_pushes_and_opens_the_pr(
        self,
        git_workspace: Any,
        task: AgentTask,
        scripted_provider: Any,
        answer_completion: Any,
        monkeypatch: Any,
    ) -> None:
        approve_change(task, MODIFY_APP_PY)
        monkeypatch.setattr(
            "apps.github.client.GitHubClient.create_pull_request",
            fake_pull_request,
        )
        provider = scripted_provider(
            [answer_completion("fix: add helper function\n\nAdds a helper next to main.")]
        )

        run_execution_stage(task, provider=provider)

        task.refresh_from_db()
        assert task.status == AgentTask.Status.PUSHED
        assert task.phase == AgentTask.Phase.DONE
        assert task.commit_sha
        assert task.commit_message.startswith("fix:")
        assert task.agent_summary  # falls back to the commit subject
        assert task.diff_stat["applied"] == ["app.py"]
        # Verification ran against no toolchain and skipped cleanly.
        assert task.verification["passed"] is True
        assert task.verification["skipped"] is True
        # The PR opened and the branch row mirrors it.
        assert task.pr_number == 7
        assert task.pr_url == "https://github.com/carol/demo-app/pull/7"
        branch = Branch.objects.get(repository=task.repository, name=task.feature_branch)
        assert branch.head_sha == task.commit_sha
        assert branch.pr_number == 7
        # The change row moved through the apply gate.
        change = task.proposed_changes.get()
        assert change.status == ProposedChange.Status.APPLIED
        # Events streamed the whole journey, ending with PUSHED.
        pushed = events_of(task, RunEvent.Kind.PUSHED)
        assert len(pushed) == 1
        assert pushed[0].payload["pr_number"] == 7
        assert len(events_of(task, RunEvent.Kind.VERIFICATION)) == 1
        assert len(events_of(task, RunEvent.Kind.STATUS)) >= 2

    def test_a_failed_apply_bakes_failed_status_with_detail(
        self,
        git_workspace: Any,
        task: AgentTask,
        scripted_provider: Any,
    ) -> None:
        approve_change(task, CONFLICTING_PATCH)

        run_execution_stage(task, provider=scripted_provider([]))

        task.refresh_from_db()
        assert task.status == AgentTask.Status.FAILED
        assert task.error_code == "apply_failed"
        assert task.diff_stat["failed"] == ["app.py"]

    def test_persistent_verification_failure_ends_the_repair_loop(
        self,
        git_workspace: Any,
        task: AgentTask,
        scripted_provider: Any,
        answer_completion: Any,
        monkeypatch: Any,
    ) -> None:
        approve_change(task, MODIFY_APP_PY)

        def always_fails(
            worktree: Any, *, detection: dict[str, Any] | None = None, timeout: float = 120.0
        ) -> VerificationResult:
            return VerificationResult(
                detection=detection or {},
                outcomes=(
                    CommandOutcome(
                        kind="test",
                        command="python -m pytest",
                        passed=False,
                        returncode=1,
                        duration_seconds=0.1,
                        output="FAILED tests/test_thing.py",
                    ),
                ),
            )

        monkeypatch.setattr("apps.agent.runner.run_verification", always_fails)
        provider = scripted_provider(
            [answer_completion("I cannot fix this; the failure is out of scope.")]
        )

        run_execution_stage(task, provider=provider)

        task.refresh_from_db()
        assert task.status == AgentTask.Status.FAILED
        assert task.error_code == "verification_failed"
        assert "attempt(s)" in task.error_message

    def test_repair_round_fixes_checks_then_commits_and_pushes(
        self,
        git_workspace: Any,
        task: AgentTask,
        scripted_provider: Any,
        tool_completion: Any,
        answer_completion: Any,
        monkeypatch: Any,
    ) -> None:
        approve_change(task, MODIFY_APP_PY)
        monkeypatch.setattr(
            "apps.github.client.GitHubClient.create_pull_request",
            fake_pull_request,
        )
        calls = {"n": 0}

        def fail_then_pass(
            worktree: Any, *, detection: dict[str, Any] | None = None, timeout: float = 120.0
        ) -> VerificationResult:
            calls["n"] += 1
            if calls["n"] == 1:
                return VerificationResult(
                    detection=detection or {},
                    outcomes=(
                        CommandOutcome(
                            kind="test",
                            command="python -m pytest",
                            passed=False,
                            returncode=1,
                            duration_seconds=0.1,
                            output="FAILED: helper returns the wrong value",
                        ),
                    ),
                )
            return VerificationResult(detection=detection or {}, outcomes=())

        monkeypatch.setattr("apps.agent.runner.run_verification", fail_then_pass)
        provider = scripted_provider(
            [
                tool_completion(
                    "repair_file",
                    {
                        "path": "app.py",
                        "patch": REPAIR_APP_PY,
                        "rationale": "Fix the helper's return value.",
                    },
                ),
                tool_completion("finish", {}),
                answer_completion("fix: correct helper return value"),
            ]
        )

        run_execution_stage(task, provider=provider)

        assert calls["n"] == 2  # initial check + re-check after repair
        task.refresh_from_db()
        assert task.status == AgentTask.Status.PUSHED
        assert task.phase == AgentTask.Phase.DONE
        assert task.commit_message == "fix: correct helper return value"
        # Verify ran twice, and the repair phase emitted an attempt note.
        assert len(events_of(task, RunEvent.Kind.VERIFICATION)) == 2
        repairs = events_of(task, RunEvent.Kind.PHASE)
        assert any(event.payload.get("phase") == "repair" for event in repairs)
        # The repair write is part of the final commit: no leftover conflict.
        assert task.diff_stat["applied"] == ["app.py"]

    def test_pipe_stage_never_leaves_the_task_half_done(
        self,
        git_workspace: Any,
        task: AgentTask,
        scripted_provider: Any,
        answer_completion: Any,
        monkeypatch: Any,
    ) -> None:
        """A stage that never runs to PUSHED must still be terminal."""
        approve_change(task, MODIFY_APP_PY)

        def broken_verification(
            worktree: Any, *, detection: dict[str, Any] | None = None, timeout: float = 120.0
        ) -> VerificationResult:
            raise RuntimeError("boom")

        monkeypatch.setattr("apps.agent.runner.run_verification", broken_verification)
        provider = scripted_provider([answer_completion("irrelevant")])

        run_execution_stage(task, provider=provider)

        task.refresh_from_db()
        assert task.status == AgentTask.Status.FAILED
        assert task.error_code == "internal_error"

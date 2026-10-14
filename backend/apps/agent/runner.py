"""The pipeline runner: turns a task row into executed work.

Runs inside a Celery worker (:mod:`apps.agent.tasks`) and drives both stages:

* **planning** -- ``queued -> awaiting_approval``: create the worktree, explore
  the repo, propose validated single-file diffs, and persist them as
  ``ProposedChange`` rows awaiting the user's decision;
* **execution** -- ``awaiting_approval -> pushed|failed``: apply the approved
  diffs, verify against the repo's own checks, repair failures within a bounded
  loop, commit conventionally, push, and open a pull request.

The Django database is the source of truth (Task.md Q9); this module only
advances it. Every phase transition and result is emitted on the event stream
that the SSE endpoint replays.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from apps.agent.apply import apply_approved_changes
from apps.agent.budget import Budget
from apps.agent.commit import deterministic_subject, run_commit_phase
from apps.agent.context import (
    build_repo_map,
    detect_toolchain,
    read_instruction_files,
    suggest_commands,
)
from apps.agent.errors import BudgetError, ProviderError, ProviderRateLimitError
from apps.agent.explore import run_explore
from apps.agent.prompts import (
    REASONING_EFFORT_BY_PHASE,
    PromptContext,
    phase_hint,
    system_message,
    user_task_message,
)
from apps.agent.propose import ProposeController, run_propose
from apps.agent.providers.base import Provider
from apps.agent.providers.groq import GroqProvider
from apps.agent.repair import RepairController, run_repair
from apps.agent.tools import ToolRegistry
from apps.agent.verify import VerificationResult, run_verification
from apps.agent_tasks import events
from apps.agent_tasks.models import AgentTask, Message, ProposedChange, RunEvent
from apps.git.errors import GitCommandError
from apps.git.patch import diffstat
from apps.git.worktree import changed_files, create_worktree, push_branch
from apps.repositories.models import Branch
from config.observability import get_logger

logger = get_logger(__name__)

__all__: list[str] = [
    "build_provider",
    "run_execution_stage",
    "run_planning_stage",
]


def build_provider() -> GroqProvider:
    """The production provider, configured entirely from settings."""
    return GroqProvider(
        api_key=settings.GROQ_API_KEY,
        base_url=settings.GROQ_BASE_URL,
        model=settings.GROQ_MODEL,
        timeout_seconds=settings.GROQ_TIMEOUT_SECONDS,
        max_retries=settings.GROQ_MAX_RETRIES,
        temperature=settings.GROQ_TEMPERATURE,
        top_p=settings.GROQ_TOP_P,
    )


# ── stage entry points ────────────────────────────────────────────────────────


def run_planning_stage(task: AgentTask, *, provider: Provider | None = None) -> None:
    """Run a queued task through explore and propose to ``awaiting_approval``."""
    token = _task_token(task)
    owns = provider is None
    if provider is None:
        provider = build_provider()
    try:
        _run_stage(task, provider, token, _plan)
    finally:
        if owns:
            _close(provider)


def run_execution_stage(task: AgentTask, *, provider: Provider | None = None) -> None:
    """Run an approved task through apply -> verify -> repair -> commit -> push."""
    token = _task_token(task)
    owns = provider is None
    if provider is None:
        provider = build_provider()
    try:
        _run_stage(task, provider, token, _execute)
    finally:
        if owns:
            _close(provider)


# ── planning ──────────────────────────────────────────────────────────────────


def _plan(task: AgentTask, provider: Provider, token: str, budget: Budget) -> None:
    events.set_status(task, AgentTask.Status.RUNNING)
    events.set_phase(task, AgentTask.Phase.PLAN)

    worktree = create_worktree(
        repository=task.repository,
        task_id=task.pk,
        branch=task.feature_branch,
        token=token,
    )
    detection = detect_toolchain(worktree)
    repo_map = build_repo_map(worktree)
    instructions = read_instruction_files(worktree)
    context = PromptContext(
        repository=task.repository.full_name,
        base_branch=task.base_branch,
        feature_branch=task.feature_branch,
        repo_map=repo_map.text,
        stack=detection,
        instructions=instructions,
        toolchain_commands=suggest_commands(detection),
    )
    system = system_message(context)
    task.detected_stack = detection
    task.save(update_fields=["detected_stack", "updated_at"])

    events.set_phase(task, AgentTask.Phase.EXPLORE, note=phase_hint("explore"))
    explore = run_explore(
        provider=provider,
        registry=ToolRegistry(worktree),
        system=system,
        task=user_task_message(_task_prompt(task)),
        budget=budget,
        reasoning_effort=REASONING_EFFORT_BY_PHASE["explore"],
        max_tool_calls=settings.AGENT_MAX_TOOL_CALLS,
    )
    _record_usage(task, budget)

    events.set_phase(task, AgentTask.Phase.PROPOSE, note=phase_hint("propose"))
    controller = ProposeController(worktree, max_repairs=settings.AGENT_MAX_REPAIR_ATTEMPTS)
    propose = run_propose(
        provider=provider,
        controller=controller,
        system=system,
        task=user_task_message(_propose_prompt(task)),
        budget=budget,
        reasoning_effort=REASONING_EFFORT_BY_PHASE["propose"],
        max_tool_calls=settings.AGENT_MAX_TOOL_CALLS,
    )
    _record_usage(task, budget)

    _persist_transcript(task, [*explore.messages, *propose.messages], system=system)

    with transaction.atomic():
        if not propose.proposals:
            _fail(task, "no_changes_proposed", "The agent did not propose any changes.")
            return

        for index, proposal in enumerate(propose.proposals):
            additions, deletions = diffstat(proposal.patch)
            row = ProposedChange.objects.create(
                task=task,
                order_index=index,
                path=proposal.path,
                change_type=proposal.change_type,
                patch=proposal.patch,
                rationale=proposal.rationale,
                additions=additions,
                deletions=deletions,
                status=ProposedChange.Status.PENDING,
            )
            events.announce_change(task, row)

        task.agent_summary = propose.summary
        task.agent_reasoning = _collapse_reasoning([*explore.reasoning, *propose.reasoning])
        task.save(update_fields=["agent_summary", "agent_reasoning", "updated_at"])
        events.set_status(task, AgentTask.Status.AWAITING_APPROVAL)
        events.set_phase(task, AgentTask.Phase.AWAIT_APPROVAL)


# ── execution ─────────────────────────────────────────────────────────────────


def _execute(task: AgentTask, provider: Provider, token: str, budget: Budget) -> None:
    worktree = create_worktree(
        repository=task.repository,
        task_id=task.pk,
        branch=task.feature_branch,
        token=token,
    )
    detection = detect_toolchain(worktree)
    system = system_message(_context_for(task, detection))

    events.set_status(task, AgentTask.Status.RUNNING)
    events.set_phase(task, AgentTask.Phase.APPLY)

    apply_summary = apply_approved_changes(task, worktree)
    task.diff_stat = apply_summary.as_dict()
    task.save(update_fields=["diff_stat", "updated_at"])
    if not apply_summary.success:
        detail = "; ".join(f"{o.path}: {o.detail}" for o in apply_summary.failed)
        _fail(task, "apply_failed", detail)
        return

    events.set_phase(task, AgentTask.Phase.VERIFY)
    verification = run_verification(worktree, detection=detection)
    _store_verification(task, verification)

    attempts = 0
    while not verification.passed and attempts < settings.AGENT_MAX_REPAIR_ATTEMPTS:
        attempts += 1
        events.set_phase(task, AgentTask.Phase.REPAIR, note=f"attempt {attempts}")
        controller = RepairController(worktree, max_repairs=settings.AGENT_MAX_REPAIR_ATTEMPTS)
        repair = run_repair(
            provider=provider,
            controller=controller,
            system=system,
            task=user_task_message(_repair_prompt(task, worktree, verification)),
            budget=budget,
            reasoning_effort=REASONING_EFFORT_BY_PHASE["repair"],
            max_tool_calls=settings.AGENT_MAX_TOOL_CALLS,
        )
        _record_usage(task, budget)
        if repair.reasoning:
            task.agent_reasoning = _collapse_reasoning([task.agent_reasoning, *repair.reasoning])
            task.save(update_fields=["agent_reasoning", "updated_at"])
        verification = run_verification(worktree, detection=detect_toolchain(worktree))
        _store_verification(task, verification)
        if not controller.applied:
            # Nothing landed; another round would only replay the same checks.
            break

    if not verification.passed:
        _fail(task, "verification_failed", _verification_failure_text(verification))
        return

    events.set_phase(task, AgentTask.Phase.COMMIT)
    applied_types = [
        change.change_type
        for change in task.proposed_changes.filter(status=ProposedChange.Status.APPLIED)
    ]
    commit_result = run_commit_phase(
        provider=provider,
        system=system,
        prompt=user_task_message(_commit_prompt(task, verification)),
        worktree=worktree,
        budget=budget,
        reasoning_effort=REASONING_EFFORT_BY_PHASE["commit"],
        fallback_message=deterministic_subject(applied_types, task.agent_summary or ""),
    )
    _record_usage(task, budget)
    if commit_result.sha is None:
        _fail(task, "nothing_to_commit", "No changes were staged to commit.")
        return
    task.commit_sha = commit_result.sha
    task.commit_message = commit_result.message
    task.agent_summary = task.agent_summary or commit_result.message.splitlines()[0]
    task.save(update_fields=["commit_sha", "commit_message", "agent_summary", "updated_at"])

    events.set_phase(task, AgentTask.Phase.PUSH)
    try:
        push_branch(worktree, branch=task.feature_branch, token=token)
    except GitCommandError:
        # A crashed worker may have pushed already; --force-with-lease is still
        # safe against a concurrent human push (Task.md Q15).
        logger.info("push_retry_force_with_lease", task_id=task.pk)
        push_branch(worktree, branch=task.feature_branch, token=token, force=True)

    pr = _open_pull_request(task, token, worktree)
    _record_branch(task, pr)
    events.emit_event(
        task,
        RunEvent.Kind.PUSHED,
        {
            "branch": task.feature_branch,
            "commit_sha": task.commit_sha,
            "pr_number": pr.get("number"),
            "pr_url": pr.get("html_url", ""),
        },
    )
    events.set_status(task, AgentTask.Status.PUSHED)
    events.set_phase(task, AgentTask.Phase.DONE)


# ── prompts ───────────────────────────────────────────────────────────────────


def _task_prompt(task: AgentTask) -> str:
    """The prompt, followed by any follow-up instructions already on record."""
    content = task.prompt
    followups = [
        message.content
        for message in Message.recent_for_prompt(task, limit=8)
        if message.role == Message.Role.USER and message.content != task.prompt
    ]
    if followups:
        content += "\n\nFollow-up instructions:\n" + "\n".join(followups)
    return content


def _propose_prompt(task: AgentTask) -> str:
    return f"{_task_prompt(task)}\n\n{phase_hint('propose')}"


def _repair_prompt(task: AgentTask, worktree: Path, verification: VerificationResult) -> str:
    lines = [
        "The checks below failed after the approved changes were applied. "
        "Fix ONLY what the checks report, using repair_file with corrected "
        "unified diffs. Do not broaden the change and do not touch files that "
        "already pass.",
        "",
        "## Failing checks",
    ]
    for outcome in verification.failing():
        lines.append(f"### {outcome.kind} — `{outcome.command}` (exit {outcome.returncode})")
        lines.append(outcome.output[:4000])
    lines.append("")
    lines.append("## Files this run changed (current contents)")
    for path in changed_files(worktree, base=task.base_branch):
        full = worktree / path
        if not full.is_file():
            continue
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines.append(f"### {path}")
        lines.append(f"```\n{content[:6000]}\n```")
    return "\n".join(lines)


def _commit_prompt(task: AgentTask, verification: VerificationResult) -> str:
    lines = [
        "Write a conventional commit message (subject line, blank line, body) "
        "describing the behaviour of the approved changes. Return only the "
        "message itself.",
        "",
        "## Changes",
    ]
    for change in task.proposed_changes.order_by("order_index", "id"):
        if change.status not in {ProposedChange.Status.APPLIED, ProposedChange.Status.APPROVED}:
            continue
        lines.append(
            f"- {change.change_type} {change.path} ({change.additions}+ {change.deletions}-)"
        )
    if task.agent_summary:
        lines += ["", "## Summary", task.agent_summary]
    if verification.outcomes:
        lines += ["", "## Checks"]
        lines += [
            f"- {outcome.kind}: {'passed' if outcome.passed else 'failed'}"
            for outcome in verification.outcomes
        ]
    return "\n".join(lines)


# ── PR / push helpers ─────────────────────────────────────────────────────────


def _open_pull_request(task: AgentTask, token: str, worktree: Path) -> dict[str, Any]:
    from apps.github.client import GitHubClient

    changed = changed_files(worktree, base=task.base_branch)
    with GitHubClient(token) as client:
        return client.create_pull_request(
            task.repository.full_name,
            head=task.feature_branch,
            base=task.base_branch,
            title=_pr_title(task),
            body=_render_pr_body(task, changed),
        )


def _pr_title(task: AgentTask) -> str:
    if task.title:
        return task.title[:120]
    if task.commit_message:
        return task.commit_message.splitlines()[0][:120]
    return f"CodePilotAI: {task.slug}"


def _render_pr_body(task: AgentTask, changed: list[str]) -> str:
    lines = [(task.agent_summary or "Automated change from CodePilotAI.").strip(), ""]
    if changed:
        lines += ["## Files changed", ""]
        lines += [f"- `{path}`" for path in changed]
        lines += [""]
    outcomes = (task.verification or {}).get("outcomes") or []
    if outcomes:
        lines += ["## Checks", ""]
        for outcome in outcomes:
            mark = "passed" if outcome.get("passed") else "failed"
            lines.append(
                f"- {outcome.get('kind')}: {mark} "
                f"(`{outcome.get('command')}`, {outcome.get('duration_seconds')}s)"
            )
        lines.append("")
    lines.append("_Generated by CodePilotAI._")
    return "\n".join(lines)


def _record_branch(task: AgentTask, pr: dict[str, Any]) -> None:
    Branch.objects.filter(repository=task.repository, name=task.feature_branch).update(
        head_sha=task.commit_sha or "",
        pr_number=pr.get("number"),
        pr_url=pr.get("html_url", ""),
        pr_state="open",
    )
    task.pr_number = pr.get("number")
    task.pr_url = pr.get("html_url", "")
    task.save(update_fields=["pr_number", "pr_url", "updated_at"])


# ── bookkeeping ───────────────────────────────────────────────────────────────


def _store_verification(task: AgentTask, verification: VerificationResult) -> None:
    task.verification = verification.as_dict()
    task.save(update_fields=["verification", "updated_at"])
    payload: dict[str, Any] = {"passed": verification.passed}
    if verification.skipped:
        payload["note"] = "No formatter, linter, or test commands were detected."
    else:
        payload["outcomes"] = [
            {
                "kind": outcome.kind,
                "command": outcome.command,
                "passed": outcome.passed,
                "duration_seconds": round(outcome.duration_seconds, 3),
            }
            for outcome in verification.outcomes
        ]
    events.emit_event(task, RunEvent.Kind.VERIFICATION, payload)


def _persist_transcript(
    task: AgentTask, messages: list[Any], *, system: SystemMessage | None = None
) -> None:
    """Store the run's conversation on the task.

    Runs only on the first planning pass: a retry must not double the threaded
    record. User turns already live on the task row (``prompt`` / Message
    rows), so only the assistant/system turns are stored.
    """
    if task.messages.exists():
        return
    rows: list[Message] = []
    if system is not None:
        rows.append(Message(task=task, role=Message.Role.SYSTEM, content=str(system.content)))
    for message in messages:
        if isinstance(message, SystemMessage):
            continue
        if isinstance(message, HumanMessage):
            continue
        if isinstance(message, AIMessage):
            parts: list[str] = []
            if message.content:
                parts.append(str(message.content))
            for call in message.tool_calls or []:
                parts.append(f"[tool: {call.get('name', '?')}]")
            rows.append(
                Message(
                    task=task,
                    role=Message.Role.ASSISTANT,
                    content="\n".join(parts) or "…",
                    metadata={"kind": "assistant"},
                )
            )
        elif isinstance(message, ToolMessage):
            rows.append(
                Message(
                    task=task,
                    role=Message.Role.ASSISTANT,
                    content=f"↳ tool: {message.content}",
                    metadata={"kind": "tool"},
                )
            )
    if rows:
        Message.objects.bulk_create(rows)


def _record_usage(task: AgentTask, budget: Budget) -> None:
    snapshot = budget.snapshot()
    task.record_usage(
        input_tokens=snapshot.input_tokens,
        output_tokens=snapshot.output_tokens,
        reasoning_tokens=snapshot.reasoning_tokens,
        cached_tokens=snapshot.cached_tokens,
    )
    task.llm_calls += snapshot.llm_calls
    task.tool_calls += snapshot.tool_calls
    task.save(
        update_fields=[
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "cached_tokens",
            "llm_calls",
            "tool_calls",
            "cost_usd",
            "updated_at",
        ]
    )


def _fail(task: AgentTask, error_code: str, error_message: str) -> None:
    logger.info("agent_task_failed", task_id=task.pk, error_code=error_code)
    events.set_status(
        task,
        AgentTask.Status.FAILED,
        error_code=error_code,
        error_message=_clip(error_message),
    )


def _run_stage(
    task: AgentTask,
    provider: Provider,
    token: str,
    fn: Any,
) -> None:
    """Run one stage, mapping every failure to a terminal task state."""
    budget = Budget(settings.AGENT_TOKEN_BUDGET, settings.AGENT_WALL_CLOCK_SECONDS)
    try:
        fn(task, provider, token, budget)
    except BudgetError as exc:
        logger.warning("agent_budget_exhausted", task_id=task.pk, error=str(exc))
        _fail(task, "budget_exhausted", str(exc))
    except (ProviderError, ProviderRateLimitError) as exc:
        logger.warning("agent_provider_error", task_id=task.pk, error=str(exc))
        _fail(task, "provider_error", str(exc))
    except Exception:
        logger.exception("agent_stage_failed", task_id=task.pk, phase=task.phase)
        _fail(
            task,
            "internal_error",
            "The agent run failed unexpectedly; details have been logged.",
        )


# ── helpers ───────────────────────────────────────────────────────────────────


def _context_for(task: AgentTask, detection: dict[str, Any]) -> PromptContext:
    return PromptContext(
        repository=task.repository.full_name,
        base_branch=task.base_branch,
        feature_branch=task.feature_branch,
        repo_map="",
        stack=detection,
        instructions=(),
        toolchain_commands=suggest_commands(detection),
    )


def _task_token(task: AgentTask) -> str:
    return task.user.github_access_token


def _collapse_reasoning(chunks: list[str], limit: int = 30_000) -> str:
    text = "\n\n".join(str(chunk).strip() for chunk in chunks if chunk and str(chunk).strip())
    return text[:limit]


def _verification_failure_text(verification: VerificationResult) -> str:
    lines: list[str] = []
    for outcome in verification.failing():
        lines.append(
            f"{outcome.kind} failed (`{outcome.command}`, exit {outcome.returncode}) "
            f"after {settings.AGENT_MAX_REPAIR_ATTEMPTS} repair attempt(s)."
        )
        lines.append(outcome.output[:1200])
    return "\n".join(lines)


def _clip(text: str, limit: int = 2000) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _close(provider: Provider) -> None:
    close = getattr(provider, "close", None)
    if callable(close):
        close()

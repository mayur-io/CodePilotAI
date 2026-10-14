"""Read-only operations dashboard views.

The console is deliberately **platform-wide** and **staff-only**: it is an
internal surface for answering "what happened on this deployment?", not a second
product UI. CodePilotAI has no password login (a user *is* their GitHub
identity), so an operator is provisioned out-of-band with a Django password and
signs in through a normal session; see the README.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from functools import wraps
from typing import Any

from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.agent_tasks.models import AgentTask

#: Rows per page on the task list.
PAGE_SIZE = 25
#: Upper bound on stream rows rendered for one task, so a chatty run cannot
#: make the detail page unbounded.
EVENT_PREVIEW_LIMIT = 500


def _is_operator(user: Any) -> bool:
    """True only for an active staff account."""
    return bool(user.is_authenticated and user.is_active and user.is_staff)


def operator_required(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Gate a view to staff; anonymous and non-staff visitors go to the login page."""

    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not _is_operator(request.user):
            return redirect_to_login(request.get_full_path(), reverse("dashboard:login"))
        return view(request, *args, **kwargs)

    return wrapped


@operator_required
@require_GET
def home(request: HttpRequest) -> HttpResponse:
    """Overview: task counts by status, usage totals, and the newest tasks."""
    tasks = AgentTask.objects.all()
    counts = dict.fromkeys(AgentTask.Status.values, 0)
    for row in tasks.values("status").annotate(count=Count("id")):
        counts[row["status"]] = row["count"]
    usage = tasks.aggregate(
        cost=Sum("cost_usd"),
        input_tokens=Sum("input_tokens"),
        output_tokens=Sum("output_tokens"),
        reasoning_tokens=Sum("reasoning_tokens"),
        cached_tokens=Sum("cached_tokens"),
    )
    context: dict[str, Any] = {
        "active_nav": "home",
        "task_total": sum(counts.values()),
        "status_rows": [
            {"value": value, "label": label, "count": counts[value]}
            for value, label in AgentTask.Status.choices
        ],
        "usage": {
            "cost": usage["cost"] or Decimal("0"),
            "tokens": (usage["input_tokens"] or 0)
            + (usage["output_tokens"] or 0)
            + (usage["reasoning_tokens"] or 0),
            "cached": usage["cached_tokens"] or 0,
        },
        "recent": tasks.select_related("repository", "user")[:10],
    }
    return render(request, "dashboard/home.html", context)


@operator_required
@require_GET
def task_list(request: HttpRequest) -> HttpResponse:
    """Paginated task list with status and free-text filters."""
    tasks = AgentTask.objects.select_related("repository", "user")
    status = request.GET.get("status", "")
    query = request.GET.get("q", "").strip()
    if status in dict(AgentTask.Status.choices):
        tasks = tasks.filter(status=status)
    if query:
        tasks = tasks.filter(
            Q(title__icontains=query)
            | Q(prompt__icontains=query)
            | Q(repository__full_name__icontains=query)
        )
    page = Paginator(tasks.order_by("-created_at"), PAGE_SIZE).get_page(request.GET.get("page"))
    context: dict[str, Any] = {
        "active_nav": "tasks",
        "page": page,
        "status_choices": AgentTask.Status.choices,
        "selected_status": status,
        "query": query,
    }
    return render(request, "dashboard/task_list.html", context)


@operator_required
@require_GET
def task_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Everything recorded for one task: result, usage, verification, and stream."""
    task = get_object_or_404(AgentTask.objects.select_related("repository", "user"), pk=pk)
    events = list(task.events.order_by("seq")[:EVENT_PREVIEW_LIMIT])
    context: dict[str, Any] = {
        "active_nav": "tasks",
        "task": task,
        "changes": task.proposed_changes.select_related("decided_by").order_by("order_index", "id"),
        "verification": task.verification or {},
        "diff_stat": task.diff_stat or {},
        "events": events,
        "events_truncated": task.events.count() > len(events),
        "messages": task.messages.order_by("created_at")[:50],
    }
    return render(request, "dashboard/task_detail.html", context)

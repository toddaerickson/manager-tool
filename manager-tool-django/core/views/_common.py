"""Views: _common."""

import os

from django.conf import settings as _settings
from django.contrib.auth.decorators import login_required
from django.http import (
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseNotFound,
    JsonResponse,
)
from django.shortcuts import redirect, render

from core.models import (
    ActionItem,
    Delegation,
    Feedback,
    Goal,
    OneOnOneSession,
)


def health(request):
    """Public, unauthenticated health + version endpoint.

    Reports the deployed git SHA so a deploy can be confirmed exactly
    (the gap the old /verify-deploy left). Render injects RENDER_GIT_COMMIT
    at build/run time; locally it falls back to "unknown"."""
    return JsonResponse({
        "status": "ok",
        "git_sha": os.environ.get("RENDER_GIT_COMMIT", "unknown"),
    })


def hello(request):
    """Public landing page. Redirects authenticated users to dashboard
    instead of showing their email in a plaintext response (S12)."""
    if request.user.is_authenticated:
        return redirect("dashboard")
    body = (
        "Manager Tool — Django scaffold.\n"
        "Sign in: /accounts/google/login/\n"
    )
    return HttpResponse(body, content_type="text/plain")


def sentry_debug(request):
    """Trigger a deliberate exception so Sentry can prove it captures errors.
    Disabled in production (S2)."""
    if getattr(_settings, "IS_PROD", False):
        return HttpResponseNotFound()
    raise ZeroDivisionError("sentry-debug: deliberate test exception")


@login_required
def dashboard(request):
    """Phase 3 → 4: template-rendered dashboard with a Tailwind sidebar
    layout. The overview panel loads via HTMX (see dashboard.html)."""
    if request.manager is None:
        return HttpResponseForbidden(
            "No manager profile is linked to this account. "
            "Ask an administrator to create one."
        )
    return render(request, "dashboard.html")


def _require_manager(request):
    """Returns (manager, None) if OK; (None, response) to short-circuit."""
    if request.manager is None:
        return None, HttpResponseForbidden(
            "No manager profile is linked to this account."
        )
    return request.manager, None


def _parse_member_filter(request):
    """Safe integer parse of ?member= query param. Returns int or None."""
    raw = request.GET.get("member", "")
    if raw:
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def get_member_context(manager_id, team_member):
    """Context panel data for a team member.

    Returns a dict of querysets for the meeting detail page's side column.
    Reusable across meeting detail and team pages.
    """
    return {
        "open_delegations": Delegation.objects.for_manager(manager_id).filter(
            team_member=team_member, status="active",
        ),
        "recent_feedback": Feedback.objects.for_manager(manager_id).filter(
            team_member=team_member,
        ).order_by("-created_at")[:5],
        "active_goals": Goal.objects.for_manager(manager_id).filter(
            team_member=team_member,
        ).exclude(status__in=["met", "not_met"]),
        "open_actions": ActionItem.objects.for_manager(manager_id).filter(
            one_on_one_session__team_member=team_member,
            status__in=["pending", "in_progress"],
        ),
        "last_4_meetings": OneOnOneSession.objects.for_manager(manager_id).filter(
            team_member=team_member, status="completed",
        ).order_by("-session_date")[:4],
    }

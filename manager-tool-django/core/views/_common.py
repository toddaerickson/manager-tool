"""Views: _common."""

import logging
import os

from django.conf import settings as _settings
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import (
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

logger = logging.getLogger(__name__)


def health(request):
    """Public, unauthenticated health + version endpoint.

    Reports the deployed git SHA so a deploy can be confirmed exactly
    (the gap the old /verify-deploy left). Render injects RENDER_GIT_COMMIT
    at build/run time; locally it falls back to "unknown".

    Also proves the database is reachable: a process that boots but
    can't reach Neon must NOT report healthy, or Render's health check
    keeps routing traffic to a service that 500s on every real page."""
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        db_ok = False
        logger.exception("health check: database unreachable")
    return JsonResponse(
        {
            "status": "ok" if db_ok else "error",
            "db": "ok" if db_ok else "unreachable",
            "git_sha": os.environ.get("RENDER_GIT_COMMIT", "unknown"),
        },
        status=200 if db_ok else 503,
    )


def hello(request):
    """Public landing page. Authenticated users go straight to dashboard
    instead of seeing the marketing page. Anonymous users get a designed
    landing with a Google sign-in CTA and the deploy SHA in the footer."""
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "landing.html", {
        "git_sha": os.environ.get("RENDER_GIT_COMMIT", "unknown"),
    })


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
        "open_actions": ActionItem.objects.active_for_manager(manager_id).filter(
            one_on_one_session__team_member=team_member,
            status__in=["pending", "in_progress"],
        ),
        "last_4_meetings": OneOnOneSession.objects.for_manager(manager_id).filter(
            team_member=team_member, status="completed",
        ).order_by("-session_date")[:4],
    }

"""Views: _common."""

from django.conf import settings as _settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseNotFound
from django.shortcuts import redirect, render


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

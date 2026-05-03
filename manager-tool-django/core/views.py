from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden

from .models import TeamMember


def hello(request):
    """Public landing page. Shows a sign-in link if anonymous, otherwise
    a link to the dashboard."""
    if request.user.is_authenticated:
        body = (
            f"Hello {request.user.email or 'authenticated user'} — "
            f"go to /dashboard/ to see your team.\n"
        )
    else:
        body = (
            "Manager Tool — Django scaffold.\n"
            "Sign in: /accounts/google/login/\n"
        )
    return HttpResponse(body, content_type="text/plain")


def sentry_debug(request):
    """Trigger a deliberate exception so Sentry can prove it captures errors.

    Phase 1 → 2 gate: a hit on this URL must show up in the Sentry dashboard
    within 60 seconds.
    """
    raise ZeroDivisionError("sentry-debug: deliberate test exception")


@login_required
def dashboard(request):
    """Phase 3 gate-verification view.

    Demonstrates the bridge from request.user (allauth-managed) to
    request.manager (the existing Manager row, attached by
    ManagerBridgeMiddleware) to a per-tenant query via TenantManager.
    """
    if request.manager is None:
        # Logged in via Google but no Manager row matches the email.
        # Phase 5 will add an onboarding flow; for now this is a 403.
        return HttpResponseForbidden(
            f"No manager profile is linked to {request.user.email}. "
            "Ask an administrator to create one."
        )

    member_count = TeamMember.objects.for_manager(request.manager.id).count()
    body = (
        f"Signed in as: {request.user.email}\n"
        f"Manager: {request.manager.display_name} (id={request.manager.id})\n"
        f"Team members: {member_count}\n"
        f"Sign out: /accounts/logout/\n"
    )
    return HttpResponse(body, content_type="text/plain")

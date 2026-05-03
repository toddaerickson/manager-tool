"""Bridge middleware: maps allauth's Django User to the existing Manager row.

The Streamlit app's domain model is `manager_id`-scoped everywhere
(audit C1). allauth gives us `request.user` (a Django auth User created
by Google OAuth). This middleware looks up the corresponding Manager row
by email and attaches it as `request.manager`, so views can do:

    if request.manager is None:
        return HttpResponseForbidden("No manager profile linked")
    qs = TeamMember.objects.for_manager(request.manager.id)

Email match is case-insensitive (Google sometimes normalizes case;
existing managers.email rows might have any casing). If multiple Manager
rows match an email — shouldn't happen in practice, but the schema
doesn't enforce uniqueness — we take the first one and log a warning.

If no Manager row matches (Google user has no profile in this app),
request.manager is None and the view layer is responsible for the 403.
"""

import logging

from .models import Manager

logger = logging.getLogger(__name__)


class ManagerBridgeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.manager = self._resolve_manager(request)
        return self.get_response(request)

    def _resolve_manager(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        email = (user.email or "").strip().lower()
        if not email:
            return None
        matches = list(Manager.objects.filter(email__iexact=email)[:2])
        if not matches:
            return None
        if len(matches) > 1:
            logger.warning(
                "Multiple Manager rows match email %s; using id=%s",
                email, matches[0].id,
            )
        return matches[0]

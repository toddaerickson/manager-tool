"""Views: settings_views."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from core.forms import (
    ManagerSettingsForm,
)
from core.views._common import _require_manager

# ============================================================
# Phase 5.7 — Settings
# ============================================================


@login_required
def settings_page(request):
    """Manager settings: display name, timezone. Email is read-only
    (set by Google OAuth). Password management is handled by allauth.
    SMTP and API key config deferred to Phase 6."""
    manager, err = _require_manager(request)
    if err:
        return err
    if request.method == "POST":
        form = ManagerSettingsForm(request.POST, instance=manager)
        if form.is_valid():
            form.save()
            return redirect("settings")
    else:
        form = ManagerSettingsForm(instance=manager)
    return render(request, "settings.html", {
        "form": form,
        "manager": manager,
    })



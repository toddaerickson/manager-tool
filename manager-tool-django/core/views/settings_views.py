"""Views: settings page + on-demand digest send."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.forms import (
    ConfigSettingsForm,
    ManagerSettingsForm,
)
from core.services.config import (
    SENSITIVE_KEYS,
    get_all_config,
    get_config,
    has_config,
    set_config,
)
from core.services.digest import send_weekly_digest
from core.views._common import _require_manager

# ============================================================
# Phase 6 — Settings: profile + AI key + SMTP + digest test
# ============================================================

CONFIG_FIELDS = (
    "anthropic_api_key",
    "manager_name",
    "manager_email",
    "smtp_server",
    "smtp_port",
    "smtp_user",
    "smtp_password",
    "inbox_imap_user",
    "inbox_imap_password",
    "inbox_allowed_senders",
)

# Password-style fields: a blank submission means "keep existing".
_KEEP_IF_BLANK = {"anthropic_api_key", "smtp_password", "inbox_imap_password"}


@login_required
def settings_page(request):
    """Settings page: profile (Manager model), API/SMTP config (Config
    table, sensitive values encrypted), current-config readout, and a
    button to send the weekly digest on demand."""
    manager, err = _require_manager(request)
    if err:
        return err

    if request.method == "POST":
        profile_form = ManagerSettingsForm(request.POST, instance=manager)
        config_form = ConfigSettingsForm(request.POST)
        if profile_form.is_valid() and config_form.is_valid():
            profile_form.save()
            for field in CONFIG_FIELDS:
                value = config_form.cleaned_data.get(field, "")
                if field in _KEEP_IF_BLANK:
                    if value:
                        set_config(field, manager.id, value)
                else:
                    set_config(field, manager.id, value)
            messages.success(request, "Settings saved.")
            return redirect("settings")
    else:
        profile_form = ManagerSettingsForm(instance=manager)
        # Pre-fill non-sensitive config fields from the DB. Sensitive
        # fields stay blank — we never re-display secrets.
        config_initial = {}
        all_config = get_all_config(manager.id)
        for field in CONFIG_FIELDS:
            if field in _KEEP_IF_BLANK:
                continue
            config_initial[field] = all_config.get(field, "")
        config_form = ConfigSettingsForm(initial=config_initial)

    return render(request, "settings.html", {
        "form": profile_form,
        "config_form": config_form,
        "manager": manager,
        "current_config": get_all_config(manager.id),
        "has_anthropic_key": has_config("anthropic_api_key", manager.id),
        "has_smtp_password": has_config("smtp_password", manager.id),
        "has_imap_password": has_config("inbox_imap_password", manager.id),
        # Written by the poll cron every run (direct upsert, no audit
        # noise) — "is email-in alive?" is answerable from this page.
        "inbox_last_poll": get_config("inbox_last_poll", manager.id),
        "sensitive_keys": SENSITIVE_KEYS,
    })


@login_required
@require_http_methods(["POST"])
def settings_send_digest(request):
    """POST-only: send the weekly digest to the configured manager
    email immediately. Useful for verifying SMTP setup without
    waiting for the Monday cron."""
    manager, err = _require_manager(request)
    if err:
        return err
    ok, msg = send_weekly_digest(manager.id)
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("settings")

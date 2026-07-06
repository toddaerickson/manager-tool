"""Per-manager Config CRUD with auto-encryption for sensitive keys.

Keys in SENSITIVE_KEYS are encrypted on write and decrypted on read,
matching the Streamlit pattern. Non-sensitive keys round-trip as
plaintext.
"""

import logging

from core.models import Config
from core.services.encryption import (
    EncryptionUnavailableError,
    decrypt_value,
    encrypt_value,
    is_encrypted,
)

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = {
    "anthropic_api_key",
    "smtp_password",
    "google_client_secret",
    "inbox_imap_password",
}


def get_config(key, manager_id, default=None):
    """Read a single config value. Sensitive values are decrypted
    transparently. Returns `default` if the row is missing or the
    stored value is empty.

    Decryption failure also returns `default` (callers treat it as
    "not configured") but logs at ERROR: a rotated/missing
    CONFIG_ENCRYPTION_KEY must be distinguishable from a manager who
    never filled in Settings, or every cron that reads config silently
    no-ops forever — the documented render.yaml bug class."""
    try:
        row = Config.objects.get(manager_id=manager_id, key=key)
    except Config.DoesNotExist:
        return default
    value = row.value
    if not value:
        return default
    if key in SENSITIVE_KEYS or is_encrypted(value):
        try:
            value = decrypt_value(value)
        except EncryptionUnavailableError:
            logger.error(
                "Config %r for manager %s is SET but failed to decrypt "
                "(CONFIG_ENCRYPTION_KEY missing/rotated or value corrupt) "
                "— treating as unconfigured",
                key, manager_id,
            )
            return default
    return value or default


def set_config(key, manager_id, value, *, actor="user"):
    """Upsert a config value. Sensitive keys are encrypted before
    storage. Empty/None values store as an empty string (so the row
    can be 'cleared' without deletion).

    Writes an AuditLog row on real changes (no-ops when the new value
    matches the current stored value — settings_page submits all fields
    on every save, even unchanged ones). Sensitive keys NEVER have
    their value rendered in the summary; we only record set/cleared
    transitions, never the secret itself.

    `actor` defaults to "user" (the settings page). Pass actor="system"
    if a background job ever ends up calling this (none currently do).
    """
    if value is None:
        value = ""

    # Detect change against the currently-stored (decrypted) value so
    # we don't write a noisy audit row on every settings-save click.
    try:
        existing = Config.objects.get(manager_id=manager_id, key=key)
        if existing.value and (key in SENSITIVE_KEYS or is_encrypted(existing.value)):
            try:
                current = decrypt_value(existing.value)
            except EncryptionUnavailableError:
                # Treat undecryptable existing value as "different" so
                # the new write proceeds and audits a refresh.
                logger.warning(
                    "Config %r for manager %s could not be decrypted "
                    "during update; overwriting with the new value",
                    key, manager_id,
                )
                current = None
        else:
            current = existing.value or ""
    except Config.DoesNotExist:
        existing = None
        current = ""

    changed = current != value
    storage_value = value
    if value and key in SENSITIVE_KEYS:
        storage_value = encrypt_value(value)

    obj, _ = Config.objects.update_or_create(
        manager_id=manager_id,
        key=key,
        defaults={"value": storage_value},
    )

    if changed:
        # Lazy import — services/audit.py doesn't import config, but
        # importing at module top forces an app-load order that breaks
        # under certain test fixtures.
        from core.services.audit import log_mutation

        if key in SENSITIVE_KEYS:
            # Never expose the secret value in the audit summary. The
            # presence of a real value vs an empty clear is the
            # auditable signal.
            verb = "set" if value else "cleared"
            summary = f"Config.{key} {verb}"
        else:
            display = value if value else "(cleared)"
            summary = f"Config.{key} = {display}"
        log_mutation(
            manager_id, "update", "Config", obj.id, summary, actor=actor,
        )


def get_all_config(manager_id):
    """Return a dict of all config keys for the manager. Sensitive
    values are masked as '********' so this is safe to display in
    UI. Use get_config() to read the actual decrypted value."""
    rows = Config.objects.filter(manager_id=manager_id)
    out = {}
    for r in rows:
        if r.key in SENSITIVE_KEYS:
            out[r.key] = "********" if r.value else ""
        else:
            out[r.key] = r.value or ""
    return out


def has_config(key, manager_id):
    """True if a non-empty value is stored for this key."""
    try:
        row = Config.objects.get(manager_id=manager_id, key=key)
        return bool(row.value)
    except Config.DoesNotExist:
        return False

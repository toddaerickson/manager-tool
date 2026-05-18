"""Per-manager Config CRUD with auto-encryption for sensitive keys.

Keys in SENSITIVE_KEYS are encrypted on write and decrypted on read,
matching the Streamlit pattern. Non-sensitive keys round-trip as
plaintext.
"""

from core.models import Config
from core.services.encryption import (
    decrypt_value,
    encrypt_value,
    is_encrypted,
)

SENSITIVE_KEYS = {
    "anthropic_api_key",
    "smtp_password",
    "google_client_secret",
}


def get_config(key, manager_id, default=None):
    """Read a single config value. Sensitive values are decrypted
    transparently. Returns `default` if the row is missing or the
    stored value is empty."""
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
        except Exception:
            return default
    return value or default


def set_config(key, manager_id, value):
    """Upsert a config value. Sensitive keys are encrypted before
    storage. Empty/None values store as an empty string (so the row
    can be 'cleared' without deletion)."""
    if value is None:
        value = ""
    if value and key in SENSITIVE_KEYS:
        value = encrypt_value(value)
    Config.objects.update_or_create(
        manager_id=manager_id,
        key=key,
        defaults={"value": value},
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

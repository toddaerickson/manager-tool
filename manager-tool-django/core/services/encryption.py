"""Fernet-based encryption for sensitive config values.

Ported from the Streamlit `database.py` helpers (AUDIT M9 / P5).
Fail-closed: any failure to encrypt or decrypt raises rather than
silently returning plaintext/ciphertext.

Production policy: when MANAGER_TOOL_ENV=prod, CONFIG_ENCRYPTION_KEY
MUST be set in the environment. Outside prod, a key file is auto-
generated under the Django root with 0600 perms (dev convenience).
"""

import logging
import os

from cryptography.fernet import Fernet

from django.conf import settings

logger = logging.getLogger(__name__)

_ENC_PREFIX = "enc:"


class EncryptionUnavailableError(RuntimeError):
    """Raised when sensitive config cannot be encrypted or decrypted."""


def _get_fernet():
    """Return a Fernet instance. Reads CONFIG_ENCRYPTION_KEY from env;
    falls back to an on-disk keyfile in non-prod. Raises if prod and
    the env var is missing — we never auto-generate a key under a prod
    deploy because a tarball of the source would then ship both the
    key and the encrypted rows."""
    key = os.environ.get("CONFIG_ENCRYPTION_KEY")
    if not key:
        if getattr(settings, "IS_PROD", False):
            raise EncryptionUnavailableError(
                "MANAGER_TOOL_ENV=prod requires CONFIG_ENCRYPTION_KEY to "
                "be set as an environment variable. Refusing to auto-"
                "generate a key file in production."
            )
        key_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))),
            ".encryption_key",
        )
        if os.path.exists(key_path):
            with open(key_path, "r") as f:
                key = f.read().strip()
        else:
            key = Fernet.generate_key().decode()
            with open(key_path, "w") as f:
                f.write(key)
        try:
            os.chmod(key_path, 0o600)
        except OSError as e:
            logger.warning("Could not chmod %s to 0600: %s", key_path, e)
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_value(value):
    """Encrypt a sensitive string. Returns 'enc:'+ciphertext. Empty
    inputs pass through unchanged so callers can clear a field by
    saving an empty string."""
    if not value:
        return value
    f = _get_fernet()
    return _ENC_PREFIX + f.encrypt(value.encode()).decode()


def decrypt_value(value):
    """Decrypt a value previously stored via encrypt_value(). Values
    that don't carry the 'enc:' prefix are returned as-is so legacy
    rows (or non-sensitive keys mistakenly passed through here) don't
    break. Raises EncryptionUnavailableError on ANY decryption failure
    — never returns ciphertext as plaintext.

    _get_fernet() is called INSIDE the try: a present-but-malformed
    CONFIG_ENCRYPTION_KEY (bad paste into the Render dashboard) makes
    Fernet() raise a bare ValueError, and callers that catch only
    EncryptionUnavailableError must see that failure too — the same
    malformed-secret crash class as the Sentry BadDsn incident
    (commit 28328e0)."""
    if not value or not value.startswith(_ENC_PREFIX):
        return value
    try:
        f = _get_fernet()
        return f.decrypt(value[len(_ENC_PREFIX):].encode()).decode()
    except EncryptionUnavailableError:
        raise
    except Exception as e:
        logger.exception("Failed to decrypt sensitive config value")
        raise EncryptionUnavailableError(
            "Failed to decrypt sensitive config value (encryption key "
            "may be malformed, may have changed, or value is corrupt)."
        ) from e


def is_encrypted(value):
    """True if the value carries the 'enc:' prefix."""
    return bool(value) and value.startswith(_ENC_PREFIX)

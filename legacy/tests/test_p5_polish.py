"""Regression tests for AUDIT M7 / M8 / M9 (P5 polish).

M7 — dialog buttons need unique key= so two dialogs in the same render
don't collide on DuplicateWidgetID.

M8 — SMTP / Postgres credentials must not surface in error messages
shown to the UI. Postgres URLs in psycopg2 exception strings get
scrubbed; SMTP exceptions are logged with full detail and a generic
message is returned to the caller.

M9 — In production (MANAGER_TOOL_ENV=prod) the encryption key MUST come
from CONFIG_ENCRYPTION_KEY env var; auto-generating into .encryption_key
on disk is forbidden. The key file (when used in dev) is chmod 600.
"""

import os
import re
import stat
from pathlib import Path

import pytest

import database as db


ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# M7 — confirm dialog buttons have key=
# ---------------------------------------------------------------------------

class TestDialogButtonKeys:
    def test_confirm_complete_event_buttons_have_key(self):
        src = (ROOT / "web_app.py").read_text()
        # Slice the function body so we don't catch other unrelated buttons.
        idx = src.index("def confirm_complete_event")
        body = src[idx:idx + 800]
        # Both Complete and Cancel buttons must have a key=
        assert re.search(r'st\.button\("Complete".*?key=', body, re.DOTALL)
        assert re.search(r'st\.button\("Cancel".*?key=', body, re.DOTALL)

    def test_confirm_complete_action_buttons_have_key(self):
        src = (ROOT / "web_app.py").read_text()
        idx = src.index("def confirm_complete_action")
        body = src[idx:idx + 800]
        assert re.search(r'st\.button\("Complete".*?key=', body, re.DOTALL)
        assert re.search(r'st\.button\("Cancel".*?key=', body, re.DOTALL)


# ---------------------------------------------------------------------------
# M8 — credential leak via error messages
# ---------------------------------------------------------------------------

class TestPgErrorRedaction:
    def test_redacts_user_password_in_postgres_url(self):
        msg = (
            "could not connect to server at "
            "postgres://alice:hunter2@db.example.com:5432/manager_tool: "
            "Connection refused"
        )
        out = db._redact_db_credentials(msg)
        assert "alice" not in out
        assert "hunter2" not in out
        assert "***@db.example.com" in out

    def test_redacts_postgresql_scheme_too(self):
        msg = "FATAL: postgresql://x:y@host/db not reachable"
        out = db._redact_db_credentials(msg)
        assert ":y@" not in out
        assert "***@host" in out

    def test_passes_through_when_no_url(self):
        msg = "operational error: timeout"
        assert db._redact_db_credentials(msg) == msg

    def test_handles_empty(self):
        assert db._redact_db_credentials("") == ""
        assert db._redact_db_credentials(None) is None


class TestSmtpErrorMessageGeneric:
    """The generic-message contract for SMTP failures: when send fails,
    the returned tuple's message should NOT contain raw exception detail
    (host/port/auth). The full detail is logged via logger.exception."""

    def test_send_calendar_invite_returns_generic_on_smtp_error(self, monkeypatch):
        import smtplib
        import calendar_service as cal

        # Stub get_config so the SMTP path runs.
        def fake_get_config(key, manager_id=None, default=None):
            return {
                "smtp_server": "smtp.evil.example.com",
                "smtp_port": "587",
                "smtp_user": "secret-user",
                "smtp_password": "secret-pw",
                "manager_name": "Manager",
                "manager_email": "boss@example.com",
            }.get(key, default)

        monkeypatch.setattr(cal, "get_config", fake_get_config)

        class _BoomSMTP:
            def __init__(self, *a, **k):
                raise smtplib.SMTPException(
                    "Auth failed for secret-user on smtp.evil.example.com:587"
                )

        monkeypatch.setattr(smtplib, "SMTP", _BoomSMTP)

        ok, msg = cal.send_calendar_invite(
            {"title": "x", "scheduled_date": "2026-05-20",
             "scheduled_time": "10:00", "manager_id": 1},
            "alice@example.com", "Alice", manager_id=1,
        )
        assert ok is False
        # The credential and the host:port must NOT leak into the UI message.
        assert "secret-user" not in msg
        assert "secret-pw" not in msg
        assert "smtp.evil.example.com" not in msg


# ---------------------------------------------------------------------------
# M9 — production-required encryption key
# ---------------------------------------------------------------------------

class TestProductionEncryptionKeyRequired:
    def test_prod_without_env_var_refuses(self, monkeypatch, tmp_path):
        """In prod, no env var → EncryptionUnavailableError. Refuses to
        auto-generate a key file."""
        monkeypatch.setenv("MANAGER_TOOL_ENV", "prod")
        monkeypatch.delenv("CONFIG_ENCRYPTION_KEY", raising=False)
        # Point at a tmp-path-relative key file, which would otherwise be
        # auto-generated in dev mode.
        monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "x.db"))
        with pytest.raises(db.EncryptionUnavailableError):
            db._get_fernet()

    def test_prod_with_env_var_works(self, monkeypatch):
        """Env var set in prod → fernet builds normally, no file written."""
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("MANAGER_TOOL_ENV", "prod")
        monkeypatch.setenv("CONFIG_ENCRYPTION_KEY", key)
        f = db._get_fernet()
        assert f is not None
        # Roundtrip works
        token = f.encrypt(b"hello")
        assert f.decrypt(token) == b"hello"

    def test_dev_auto_generates_with_chmod_600(self, monkeypatch, tmp_path):
        """Outside prod, the key file is auto-generated and immediately
        chmod 600. Verify the resulting file's permissions."""
        monkeypatch.delenv("MANAGER_TOOL_ENV", raising=False)
        monkeypatch.delenv("CONFIG_ENCRYPTION_KEY", raising=False)
        # Move the key location into tmp_path so we don't clobber any real
        # .encryption_key.
        # _get_fernet computes the path off the database.py module dir;
        # we monkeypatch __file__ in the module to redirect.
        original_file = db.__file__
        new_dir = tmp_path
        new_file = str(new_dir / "database.py")
        new_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(db, "__file__", new_file)

        f = db._get_fernet()
        assert f is not None

        key_path = tmp_path / ".encryption_key"
        assert key_path.exists()
        mode = stat.S_IMODE(os.stat(key_path).st_mode)
        # Expect 0o600 (owner rw only)
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"

        # Restore so subsequent tests aren't affected (monkeypatch handles
        # this on teardown, but be explicit).
        monkeypatch.setattr(db, "__file__", original_file)

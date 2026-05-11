"""Shared email utilities — SMTP sending and per-manager config.

Extracted from calendar.py and digest.py to avoid duplication (#3, #4
from /review-as findings). Both services import from here.
"""

import logging
import smtplib
from email.utils import parseaddr

from core.models import Config

logger = logging.getLogger(__name__)

SMTP_TIMEOUT = 30  # seconds — prevents indefinite hangs (#9)


def get_config(key, manager_id, default=None):
    """Read a single config value for a manager."""
    try:
        row = Config.objects.get(manager_id=manager_id, key=key)
        return row.value or default
    except Config.DoesNotExist:
        return default


def get_smtp_settings(manager_id):
    """Return SMTP connection params or None if not configured."""
    server = get_config("smtp_server", manager_id)
    user = get_config("smtp_user", manager_id)
    password = get_config("smtp_password", manager_id)
    email = get_config("manager_email", manager_id)
    if not all([server, user, password, email]):
        return None
    port_str = get_config("smtp_port", manager_id, default="587")
    port = int(port_str) if port_str and str(port_str).isdigit() else 587
    name = get_config("manager_name", manager_id, default="Manager")
    return {
        "server": server,
        "port": port,
        "user": user,
        "password": password,
        "email": email,
        "name": name,
    }


def send_smtp(smtp_cfg, msg):
    """Send an email via SMTP. Returns (success, message).

    Used by both calendar invites and weekly digest.
    """
    server = None
    try:
        server = smtplib.SMTP(smtp_cfg["server"], smtp_cfg["port"],
                              timeout=SMTP_TIMEOUT)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(smtp_cfg["user"], smtp_cfg["password"])
        server.sendmail(
            smtp_cfg["email"],
            [parseaddr(msg["To"])[1]],
            msg.as_string(),
        )
        server.quit()
        server = None  # quit succeeded, no cleanup needed
        return True, f"Email sent to {msg['To']}"
    except smtplib.SMTPAuthenticationError:
        logger.exception("SMTP auth failed for %s", smtp_cfg["user"])
        return False, (
            "SMTP authentication failed. For Gmail, use an App Password: "
            "https://myaccount.google.com/apppasswords"
        )
    except smtplib.SMTPException:
        logger.exception("SMTP error")
        return False, "SMTP error. Check the server logs for details."
    except Exception:
        logger.exception("Failed to send email")
        return False, "Failed to send email. Check the server logs for details."
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass

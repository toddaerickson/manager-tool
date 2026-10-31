"""Django email backend that sends through the *recipient* manager's SMTP.

This app has no global SMTP account: outbound mail has always gone through
the per-manager credentials in the `Config` table (Fernet-encrypted
`smtp_password`), used by calendar invites and the weekly digest via
`core/services/email.py`. django-allauth's password-reset flow, however,
sends through `django.core.mail`, which needs an `EMAIL_BACKEND` — without
one Django falls back to `localhost:25` and every reset request raises
`ConnectionRefusedError` (a 500 after the token has already been issued).

This backend is that bridge. For each message it resolves the Manager row
for the recipient address (the same email -> Manager rule
`ManagerBridgeMiddleware` uses), loads that manager's SMTP settings and
hands the message to Django's own SMTP backend, so STARTTLS/SSL, AUTH and
Bcc handling stay in Django's code.

Two properties are deliberate and both are covered by tests:

* **No relay.** Credentials come from the recipient's own row, and a message
  is only sent when it has exactly one recipient. A typo, a probe address or
  a future multi-recipient caller is dropped rather than delivered through
  someone else's mailbox.
* **No exceptions.** Missing config and SMTP failures are logged (and
  reported to Sentry when a DSN is set) but never raised. allauth renders the
  same "if that address has an account, we sent a link" page for every
  address, so a 500 here would turn the reset form into an
  account-existence oracle.
"""

import logging
from email.utils import formataddr

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.smtp import EmailBackend as SMTPBackend

from core.models import Manager
from core.services.email import SMTP_TIMEOUT, get_smtp_settings

logger = logging.getLogger(__name__)

# Ports that speak implicit TLS (SMTP_SSL) instead of STARTTLS. 465 is the
# submission port Google Workspace and several providers expose; everything
# else (587, 25, custom) goes through STARTTLS like core/services/email.py.
_IMPLICIT_TLS_PORTS = {465}


def _strip_newlines(value):
    """Remove CR/LF from a value that is about to become a mail header.

    ``manager_name`` comes from the Settings form. A line break in it makes
    Django raise ``BadHeaderError`` while building the message, which this
    backend would swallow as a silent non-delivery — a config typo would look
    like a broken mail server. Sanitising keeps it a typo.
    """
    return (value or "").replace("\r", " ").replace("\n", " ").strip()


def _mask(address):
    """``ada@example.com`` -> ``a***@example.com`` — keep logs useful, not PII."""
    local, _, domain = (address or "").partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


class ManagerConfigEmailBackend(BaseEmailBackend):
    """Route each message through the SMTP account of its recipient."""

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        sent = 0
        for message in email_messages:
            if self._send_one(message):
                sent += 1
        return sent

    def _send_one(self, message):
        """Deliver one message, or return False — never raise.

        The guard is deliberately *outside* the Manager/Config lookups: a DB
        blip, a rotated ``CONFIG_ENCRYPTION_KEY`` or any other unexpected
        failure must render allauth's neutral "check your email" page, not a
        500 that tells an attacker which addresses exist.
        """
        try:
            return self._deliver(message)
        except Exception:
            logger.exception(
                "Unexpected failure sending %r; message dropped",
                message.subject,
            )
            self._report_to_sentry()
            return False

    def _deliver(self, message):
        recipient = self._sole_recipient(message)
        if recipient is None:
            return False

        manager = Manager.objects.filter(email__iexact=recipient).first()
        if manager is None:
            # Not a manager address (e.g. allauth's unknown-account notice).
            # There is no SMTP account to send from, and this app must never
            # become a relay for arbitrary addresses — drop it.
            logger.warning(
                "Dropping %r: %s has no Manager row",
                message.subject, _mask(recipient),
            )
            return False

        cfg = get_smtp_settings(manager.id)
        if cfg is None:
            logger.error(
                "Cannot send %r to manager %s: SMTP is not configured "
                "(Settings > Email). The message was not sent.",
                message.subject, manager.id,
            )
            return False

        use_ssl = cfg["port"] in _IMPLICIT_TLS_PORTS
        message.from_email = self._sender(cfg)
        try:
            backend = SMTPBackend(
                host=cfg["server"],
                port=cfg["port"],
                username=cfg["user"],
                password=cfg["password"],
                use_tls=not use_ssl,
                use_ssl=use_ssl,
                timeout=self._timeout(),
                fail_silently=False,
            )
            backend.send_messages([message])
        except Exception:
            logger.exception(
                "Sending %r to manager %s via %s:%s failed",
                message.subject, manager.id, cfg["server"], cfg["port"],
            )
            self._report_to_sentry()
            return False
        logger.info(
            "Sent %r to manager %s via %s:%s",
            message.subject, manager.id, cfg["server"], cfg["port"],
        )
        return True

    @staticmethod
    def _sole_recipient(message):
        recipients = {
            addr.strip().lower()
            for addr in message.recipients()
            if addr and addr.strip()
        }
        if len(recipients) != 1:
            logger.warning(
                "Dropping %r: expected exactly one recipient, got %s",
                message.subject, sorted(_mask(addr) for addr in recipients),
            )
            return None
        return recipients.pop()

    @staticmethod
    def _timeout():
        """Django's ``EMAIL_TIMEOUT`` when set, else the shared SMTP timeout.

        Keeping the standard setting live means ops can tune the reset-mail
        deadline with an env var instead of a code change; the fallback keeps
        this backend in step with calendar/digest mail.
        """
        return getattr(settings, "EMAIL_TIMEOUT", None) or SMTP_TIMEOUT

    @staticmethod
    def _sender(cfg):
        """Build a From: the manager's identity, when their SMTP accepts it.

        Providers (Gmail/Workspace in particular) rewrite or reject a From
        that is not the authenticated account, so fall back to the SMTP user
        and keep the display name.
        """
        address = (cfg["email"] or "").strip()
        if address.lower() != (cfg["user"] or "").strip().lower():
            logger.info(
                "manager_email %s differs from smtp_user; sending as %s",
                _mask(address), _mask(cfg["user"]),
            )
            address = cfg["user"]
        return formataddr(
            (_strip_newlines(cfg["name"]), _strip_newlines(address)),
        )

    @staticmethod
    def _report_to_sentry():
        if not getattr(settings, "SENTRY_DSN", ""):
            return
        try:
            import sentry_sdk

            # Called from an except block, so this captures the active
            # exception (SMTP auth/timeout/connection failure).
            sentry_sdk.capture_exception()
        except Exception:  # pragma: no cover - observability must not raise
            logger.debug("Sentry capture failed", exc_info=True)

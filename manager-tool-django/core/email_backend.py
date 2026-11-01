"""Django email backend that delivers through Resend's HTTP API.

Outbound mail has two users in this app, and they want different senders.
Calendar invites and the weekly digest go through the *manager's own* SMTP
account (`core/services/email.py`) because they are supposed to come from that
manager's mailbox. Account mail — allauth's password reset — is the opposite
case: it has to reach a manager who has never filled in Settings, it must come
from a domain we control, and it must not depend on a per-manager app password
that Google revokes every few months. So it goes through a hosted
transactional API instead of SMTP.

This module is the `EMAIL_BACKEND` that makes that possible. allauth sends
through `django.core.mail`, which without an explicit backend falls back to
`localhost:25` and raises `ConnectionRefusedError` *after* the reset token has
already been issued.

Two properties are deliberate and both are covered by tests:

* **No spray.** Resend accepts a list of recipients, so an unguarded backend
  would be an open relay for anyone who can reach a form. Only messages with
  exactly one recipient are sent, and that address must resolve to a `Manager`
  row (or be listed in `EMAIL_API_ALLOWED_RECIPIENTS` for operator smoke
  tests). A typo, a probe address or a future multi-recipient caller is
  dropped rather than delivered.
* **No exceptions.** A missing key, a rejected request, a timeout or any
  unexpected failure is logged (and reported to Sentry when a DSN is set) but
  never raised. allauth renders the same "if that address has an account, we
  sent a link" page for every address, so a 500 here would turn the reset form
  into an account-existence oracle.

Operator prerequisites live in the environment, not the `Config` table:
`EMAIL_API_KEY` (a Resend API key) and `DEFAULT_FROM_EMAIL` (an address on a
domain verified in Resend — an unverified domain is rejected with HTTP 403).
"""

import logging

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail.backends.base import BaseEmailBackend
from django.core.validators import validate_email

from core.models import Manager
from core.services.config import get_config

logger = logging.getLogger(__name__)

# POST https://api.resend.com/emails -> {"id": "..."} (200) or
# {"statusCode": ..., "name": ..., "message": ...} (4xx/5xx). Verified against
# the live API without a valid key: missing key -> 401 {"name":
# "missing_api_key"}, bad key -> 401 {"name": "validation_error", "message":
# "API key is invalid"}, unverified/mismatched From domain -> 403 {"name":
# "validation_error", "message": "The <domain> domain is not verified..."}.
RESEND_ENDPOINT = "https://api.resend.com/emails"

# Resend sits behind Cloudflare, which rejects requests that arrive with no
# User-Agent (or a blocked one) with 403 + error code 1010 *before* the API
# sees them — an error that looks like a domain/key problem but isn't.
# `requests` does send `python-requests/x.y`, so this is belt-and-braces:
# an integration-shaped UA we control instead of one a WAF may block.
USER_AGENT = "manager-tool-email-backend/1.0"

# A reset link is time-sensitive and a request that hangs holds a worker; the
# provider answers in well under a second. Overridden by EMAIL_TIMEOUT.
DEFAULT_TIMEOUT = 10


def _one_line(value):
    """Remove CR/LF from a value that is about to become a mail header.

    ``DEFAULT_FROM_EMAIL`` comes from the environment and ``manager_email``
    from the Settings form. A line break in either makes the request a header
    injection attempt, and since this backend never raises, a provider
    rejection would look exactly like a broken API key. Sanitising keeps it a
    typo.
    """
    return (value or "").replace("\r", " ").replace("\n", " ").strip()


def _is_address(value):
    """True for a single, syntactically valid address with no line breaks.

    Resend answers a malformed ``to``/``reply_to`` with 422 and delivers
    nothing, so a garbage value from the Settings form (``manager_email``) would
    quietly cost a manager their reset link. Everything that becomes an address
    in a request body goes through here first.
    """
    if not value or "\r" in value or "\n" in value:
        return False
    try:
        validate_email(value)
    except ValidationError:
        return False
    return True


def _mask(address):
    """``ada@example.com`` -> ``a***@example.com`` — keep logs useful, not PII."""
    local, _, domain = (address or "").partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


def _error_detail(response):
    """Resend's error body, flattened to one log-safe line."""
    try:
        body = response.json()
    except ValueError:
        return (response.text or "").strip()[:200] or "(empty body)"
    if isinstance(body, dict):
        name = body.get("name") or ""
        message = body.get("message") or ""
        detail = ": ".join(part for part in (name, message) if part)
        return detail or str(body)[:200]
    return str(body)[:200]


def _response_id(response):
    try:
        body = response.json()
    except ValueError:
        return ""
    return body.get("id", "") if isinstance(body, dict) else ""


class ResendEmailBackend(BaseEmailBackend):
    """Send Django mail through Resend's `/emails` endpoint."""

    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_key = (getattr(settings, "EMAIL_API_KEY", "") or "").strip()
        self.endpoint = getattr(settings, "EMAIL_API_URL", "") or RESEND_ENDPOINT
        self.from_email = _one_line(
            getattr(settings, "DEFAULT_FROM_EMAIL", "")
        ) or "Manager Tool <no-reply@localhost>"
        self.timeout = getattr(settings, "EMAIL_TIMEOUT", None) or DEFAULT_TIMEOUT

    # -- BaseEmailBackend --------------------------------------------------

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        if not self.api_key:
            logger.error(
                "EMAIL_API_KEY is not set: %d message(s) dropped, starting with "
                "%r. Password-reset mail cannot be delivered until it is "
                "configured.", len(email_messages), email_messages[0].subject,
            )
            return 0
        return sum(1 for message in email_messages if self._send_one(message))

    # -- internals ---------------------------------------------------------

    def _send_one(self, message):
        """Deliver one message; never propagates (see module docstring)."""
        try:
            return self._deliver(message)
        except Exception as exc:
            logger.exception(
                "Unexpected failure sending %r; message dropped", message.subject,
            )
            self._report(
                f"Resend backend crashed sending {message.subject!r}: {exc}",
                exc=exc,
            )
            return False

    def _deliver(self, message):
        recipient = self._sole_recipient(message)
        if recipient is None:
            return False
        if not _is_address(recipient):
            logger.warning(
                "Dropping %r: %r is not a usable address (invalid or contains a "
                "line break)", message.subject, _mask(recipient),
            )
            return False
        # One lookup serves both the allow decision and Reply-To routing;
        # `manager_email` decryption is not free and this runs on the request
        # path of the reset form.
        manager = self._manager(recipient)
        if manager is None and not self._allowlisted(recipient):
            logger.warning(
                "Dropping %r: %s is not a manager address and is not listed in "
                "EMAIL_API_ALLOWED_RECIPIENTS", message.subject, _mask(recipient),
            )
            return False

        attachments = getattr(message, "attachments", None) or []
        if attachments:
            logger.warning(
                "Dropping %d attachment(s) from %r: this backend sends the text "
                "and HTML parts only", len(attachments), message.subject,
            )

        payload = self._payload(message, recipient, manager)
        response = requests.post(
            self.endpoint,
            json=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            detail = _error_detail(response)
            logger.error(
                "Resend rejected %r for %s (HTTP %s): %s",
                message.subject, _mask(recipient), response.status_code, detail,
            )
            self._report(
                f"Resend rejected {message.subject!r} for {_mask(recipient)} "
                f"(HTTP {response.status_code}): {detail}"
            )
            return False
        logger.info(
            "Sent %r to %s via Resend (id=%s)",
            message.subject, _mask(recipient), _response_id(response) or "?",
        )
        return True

    @staticmethod
    def _sole_recipient(message):
        """The single envelope recipient, or ``None`` for anything else."""
        recipients = message.recipients()
        if len(recipients) != 1:
            logger.warning(
                "Dropping %r: this backend sends to exactly one recipient "
                "(got %d)", message.subject, len(recipients),
            )
            return None
        return recipients[0]

    @staticmethod
    def _manager(recipient):
        """The manager row for ``recipient``, if any (emails are not unique)."""
        return Manager.objects.filter(email__iexact=recipient).first()

    @staticmethod
    def _allowlisted(recipient):
        """True when an operator listed this address for smoke tests."""
        allowlist = getattr(settings, "EMAIL_API_ALLOWED_RECIPIENTS", "") or ""
        if isinstance(allowlist, str):
            allowlist = allowlist.split(",")
        return recipient.lower() in {
            entry.strip().lower() for entry in allowlist if entry and entry.strip()
        }

    def _payload(self, message, recipient, manager):
        text, html = self._bodies(message)
        payload = {
            "from": self.from_email,
            "to": [recipient],
            "subject": _one_line(message.subject),
        }
        if text:
            payload["text"] = text
        if html:
            payload["html"] = html
        reply_to = self._reply_to(recipient, manager)
        if reply_to:
            payload["reply_to"] = [reply_to]
        return payload

    @staticmethod
    def _bodies(message):
        """Plain text plus the HTML alternative, when the caller supplied one."""
        text = message.body or ""
        for content, mimetype in getattr(message, "alternatives", None) or []:
            if mimetype == "text/html":
                return text, content
        return text, ""

    @staticmethod
    def _reply_to(recipient, manager):
        """Route replies to the manager's own mailbox.

        Resets are sent from a no-reply address on our domain, so without this
        a manager who hits Reply talks to nobody. ``manager_email`` is free text
        from the Settings form: if it is not a valid address Resend would reject
        the whole request (422), costing the manager their link — so fall back to
        the recipient instead of passing garbage through.
        """
        if manager is None:
            return recipient
        stored = _one_line(get_config("manager_email", manager.id, default=""))
        if not stored:
            return recipient
        if not _is_address(stored):
            logger.warning(
                "Ignoring invalid manager_email for manager %s (%r); replying to "
                "the recipient instead", manager.id, stored[:40],
            )
            return recipient
        return stored

    @staticmethod
    def _report(summary, exc=None):
        """Best effort: reporting a failure must never break delivery.

        ``capture_exception()`` is only meaningful inside an ``except`` block —
        called anywhere else it uploads an event with ``exception.values: []``,
        i.e. a blank issue that says nothing about what broke. Provider
        rejections are not exceptions at all, so they go out as messages.
        """
        try:
            from sentry_sdk import capture_exception, capture_message
        except ImportError:
            logger.debug("sentry_sdk is unavailable; failure was logged only")
            return
        try:
            if exc is not None:
                capture_exception(exc)
            else:
                capture_message(summary, level="error")
        except Exception as reporting_error:  # noqa: BLE001 — never raise here
            logger.warning(
                "Sentry capture failed (%s); failure was logged only",
                reporting_error,
            )

"""Shared utilities for the core app.

Keep this file small and dependency-free — anything imported by `models.py`
or by middleware can't reach here for circular-import reasons. Helpers
that need ORM access belong in `core/services/`.
"""

import logging
import re


logger = logging.getLogger(__name__)


_DB_URL_CRED_RE = re.compile(
    r"(postgres(?:ql)?://)[^/@\s]*@",
    re.IGNORECASE,
)


def redact_db_credentials(text: str) -> str:
    """Scrub `user:password@` from any postgres URL embedded in `text`.

    psycopg sometimes echoes the full DSN in exception messages; we don't
    want that landing in error pages, structured logs, or Sentry breadcrumbs.
    Mirrors Streamlit `_redact_db_credentials` (AUDIT M8) verbatim.
    """
    if not text:
        return text
    return _DB_URL_CRED_RE.sub(r"\1***@", text)


def sentry_before_send(event, _hint):
    """Sentry `before_send` hook: redact DB URIs from exception text before
    sending. Sentry's default scrubber catches query params (`?password=`)
    but not Postgres userinfo (`user:pw@host`). Wire via
    `sentry_sdk.init(..., before_send=sentry_before_send)`.
    """
    try:
        for exc in (event.get("exception") or {}).get("values", []) or []:
            if exc.get("value"):
                exc["value"] = redact_db_credentials(exc["value"])
        if event.get("message"):
            event["message"] = redact_db_credentials(event["message"])
        for crumb in event.get("breadcrumbs", {}).get("values", []) or []:
            if crumb.get("message"):
                crumb["message"] = redact_db_credentials(crumb["message"])
    except Exception:
        # The redactor must never crash an error report — but we still want
        # a breadcrumb so a regression here is visible. `exc_info=False`
        # keeps Sentry's logging integration from looping back into here.
        logger.warning("sentry_before_send redactor failed; event sent unredacted")
    return event

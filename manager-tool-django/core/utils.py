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


def _strip_private_payloads(event):
    """Remove the parts of a Sentry event that can carry note text.

    sentry-sdk captures, by default, every stack frame's local variables
    and the HTTP request body. In this app those hold journal entries,
    1:1 notes and the exact prompt sent to Claude, with real names. The
    init flags in mt/settings.py turn both captures off; this is the
    second layer, so a future flag change or SDK default change cannot
    re-open the leak. Query strings and headers go too: /search/?q=
    carries names, and Referer/HX-Current-URL repeat it. Wired to both
    before_send (errors) and before_send_transaction (performance traces,
    which before_send never sees).
    """
    def _dicts(items):
        return [i for i in (items if isinstance(items, list) else []) if isinstance(i, dict)]

    for key in ("exception", "threads"):
        container = event.get(key)
        values = container.get("values") if isinstance(container, dict) else None
        for value in _dicts(values):
            stacktrace = value.get("stacktrace")
            frames = stacktrace.get("frames") if isinstance(stacktrace, dict) else None
            for frame in _dicts(frames):
                frame.pop("vars", None)
    request = event.get("request")
    if isinstance(request, dict):
        # Headers go whole: Referer and HX-Current-URL repeat the search
        # URL (/search/?q=<name>), and none of them is needed to debug.
        for field in ("data", "query_string", "cookies", "headers"):
            request.pop(field, None)
        if isinstance(request.get("url"), str):
            request["url"] = request["url"].split("?", 1)[0]
    # Performance transactions: span data can repeat the URL/query.
    for span in _dicts(event.get("spans")):
        data = span.get("data")
        if isinstance(data, dict):
            for k in [k for k in data if "query" in k or "url" in k]:
                data.pop(k, None)
        if isinstance(span.get("description"), str) and span.get("op", "").startswith("http"):
            span["description"] = span["description"].split("?", 1)[0]


def sentry_before_send(event, _hint):
    """Sentry `before_send` hook: strip note text (frame locals, request
    body, query string) and redact DB URIs from exception text before
    sending. Sentry's default scrubber catches query params (`?password=`)
    but not Postgres userinfo (`user:pw@host`). Wire via
    `sentry_sdk.init(..., before_send=sentry_before_send)`.
    """
    try:
        _strip_private_payloads(event)
    except Exception:
        # Fail closed: if the shape is unexpected, drop the event rather
        # than risk uploading note text. Losing one error report is the
        # cheaper failure.
        logger.warning("sentry_before_send could not strip payloads; event dropped")
        return None
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

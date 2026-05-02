"""Regression tests for web_app.py XSS hardening (AUDIT M1 / P3.1).

We cannot drive the full Streamlit render in pytest, so these tests verify
two invariants against the source file plus the underlying html.escape
contract:
  1. Every f-string that interpolates a Claude-generated suggestion or a
     stringified DB error into an `unsafe_allow_html=True` block escapes the
     untrusted portion via html.escape().
  2. html.escape() actually neutralizes the canonical XSS payloads.
"""

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_APP = ROOT / "web_app.py"


def _src() -> str:
    return WEB_APP.read_text()


def test_daily_suggestion_is_escaped_before_unsafe_html():
    """The Claude-generated suggestion must flow through html.escape() before
    it reaches an `unsafe_allow_html=True` block. Regression for AUDIT M1
    (web_app.py:353-362 in the audit report)."""
    src = _src()
    # The vulnerable pattern was a raw `{suggestion["suggestion"]}` directly
    # inside an unsafe_allow_html markdown block. Reject that form.
    raw_pattern = re.compile(
        r'\{suggestion\["suggestion"\]\}.*?unsafe_allow_html=True',
        re.DOTALL,
    )
    assert not raw_pattern.search(src), \
        "Found raw suggestion[\"suggestion\"] interpolation into unsafe_allow_html"
    # Positive: the escaped form is present.
    assert "html.escape(suggestion[\"suggestion\"])" in src


def test_pg_error_is_escaped_before_unsafe_html():
    """psycopg2 error strings may contain HTML metacharacters from a
    malformed URL or hostname; they must be escaped before being rendered
    inside the DB-failure banner."""
    src = _src()
    # Reject raw _pg_error[:N] interpolation
    assert not re.search(r"\{_pg_error\[:\d+\]\}", src), \
        "Found raw _pg_error slice interpolation into unsafe_allow_html"
    assert "html.escape(_pg_error" in src


def test_html_module_is_imported():
    """The fix relies on `html.escape`; ensure the module is imported."""
    src = _src()
    assert re.search(r"^import html\b", src, re.MULTILINE)


def test_html_escape_neutralises_canonical_xss_payloads():
    """Sanity-check the underlying escape function — guards against someone
    swapping in a partial replacement. The contract: the output must contain
    no live `<` characters and no quote characters that could break out of
    an attribute (since the suggestion text is ALSO interpolated inside
    style attributes elsewhere)."""
    payloads = [
        '<script>alert(1)</script>',
        '<img src=x onerror=alert(1)>',
        '"><svg/onload=alert(1)>',
        "'; DROP TABLE x; --",
        '</span><script>steal()</script><span>',
    ]
    for payload in payloads:
        out = html.escape(payload, quote=True)
        # No live tag-opening characters.
        assert "<" not in out, f"{payload!r} → {out!r} still contains <"
        assert ">" not in out, f"{payload!r} → {out!r} still contains >"
        # No live attribute-breaking quotes.
        assert '"' not in out, f"{payload!r} → {out!r} still contains \""
        assert "'" not in out, f"{payload!r} → {out!r} still contains '"

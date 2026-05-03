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


def test_next_step_row_escapes_button_label():
    """The Next Step row on Dashboard renders the rule-based suggestion as
    a tertiary button label. Tertiary buttons don't render HTML in labels
    today, but the suggestion text incorporates user-controlled content
    (action item descriptions, delegation tasks, event titles) and would
    surface raw HTML if Streamlit's button label rendering ever changes.
    Defense-in-depth — mirrors the Coach card escape at web_app.py:413."""
    src = _src()
    assert "html.escape(ns_text)" in src, \
        "Next Step button label must wrap rule-based suggestion in html.escape()"


def test_one_on_one_page_escapes_member_name_in_title():
    """The 1:1 page header interpolates `member["name"]` into a markdown
    string. A team member with a `<script>` in their name would otherwise
    surface as live HTML when the page renders the title."""
    src = _src()
    assert "html.escape(member[\"name\"])" in src or "name_safe = html.escape" in src, \
        "1:1 page must escape member name before interpolating into the title"


def test_one_on_one_page_escapes_carryover_followup_notes():
    """`followup_notes` from the prior session are user-controlled text.
    The carry-over banner uses `st.info(...)` with markdown-style content;
    the value must be html-escaped so a `<script>` in last week's notes
    doesn't render as live HTML in this week's banner."""
    src = _src()
    assert "html.escape(prior['followup_notes'])" in src, \
        "Carry-over banner must escape prior followup_notes"


def test_one_on_one_page_escapes_feedback_fields():
    """SBI feedback fields (situation, behavior, impact) flow into the
    'Latest feedback' expander on the 1:1 page. Each field is user-typed
    and must be escaped before rendering inside the markdown block."""
    src = _src()
    for snippet in (
        'html.escape(fb.get("situation") or "")',
        'html.escape(fb.get("behavior") or "")',
        'html.escape(fb.get("impact") or "")',
    ):
        assert snippet in src, \
            f"Feedback expander missing escape: {snippet!r}"


def test_one_on_one_past_records_escape_preview():
    """Past 1:1 records are listed with a 80-char preview of `direct_notes`.
    User-typed; must be escaped before rendering in the markdown row."""
    src = _src()
    assert "preview_safe = html.escape(preview)" in src, \
        "Past 1:1 records must escape direct_notes preview"


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

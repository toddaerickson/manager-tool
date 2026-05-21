"""Regression tests for AUDIT L-series cleanups (P5).

L2 — bcrypt rounds pinned to 12 (deterministic cost across deploys).
L7 — dead `timedelta` imports removed from gui.py and manager_tool.py.
L9 — nudge thresholds promoted from inline magic numbers to module constants.
L11 — _exec_returning_id strips trailing whitespace/semicolons before the
      `RETURNING id` concatenation so caller SQL with trailing `;` doesn't
      break on Postgres.
L12 — .streamlit/config.toml documents the expected proxy-level security
      headers (HSTS, X-Frame-Options, CSP, etc).
"""

import re
from pathlib import Path

import database as db


ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# L2 — bcrypt rounds=12 pin
# ---------------------------------------------------------------------------

def test_bcrypt_rounds_pinned():
    """The hash function explicitly passes rounds=12 to bcrypt.gensalt()."""
    src = (ROOT / "database.py").read_text()
    assert re.search(r"bcrypt\.gensalt\(rounds=12\)", src), \
        "bcrypt.gensalt() must use a pinned rounds= value"


def test_new_passwords_use_rounds_12():
    """A freshly-hashed password's bcrypt prefix encodes rounds=12.
    Format: $2b$12$... (where 12 is the cost factor)."""
    mid = db.create_manager("rounds_test", "X", "pass1234")
    mgr = db.get_manager(mid)
    assert mgr["password_hash"].startswith("$2b$12$"), \
        f"Expected $2b$12$..., got {mgr['password_hash'][:10]}"


# ---------------------------------------------------------------------------
# L7 — dead imports
# ---------------------------------------------------------------------------

def test_gui_does_not_import_timedelta():
    src = (ROOT / "gui.py").read_text()
    assert "timedelta" not in src, \
        "gui.py should not import or reference timedelta (L7)"


def test_manager_tool_does_not_import_timedelta():
    src = (ROOT / "manager_tool.py").read_text()
    # `timedelta` should only appear if it's used; the audit confirmed it
    # was only the import. Search for "timedelta" — if it shows up at all,
    # something other than the import was added (acceptable; this guards
    # against the dead-import regression).
    matches = re.findall(r"\btimedelta\b", src)
    assert not matches, \
        f"manager_tool.py contains unexpected timedelta references: {len(matches)}"


# ---------------------------------------------------------------------------
# L9 — nudge thresholds as module constants
# ---------------------------------------------------------------------------

class TestNudgeThresholdsConstants:
    def test_constants_defined(self):
        assert db.MEETING_CRITICAL_DAYS == 21
        assert db.MEETING_WARNING_DAYS == 14
        assert db.STALE_FEEDBACK_DAYS == 21

    def test_no_inline_magic_numbers_in_get_nudges(self):
        """Inside get_nudges(), the meeting-day branches should reference
        MEETING_CRITICAL_DAYS / MEETING_WARNING_DAYS, not bare 21/14."""
        src = (ROOT / "database.py").read_text()
        # Slice from `def get_nudges(` through the next def
        m = re.search(r"def get_nudges\(.*?(?=\ndef )", src, re.DOTALL)
        assert m, "Could not locate get_nudges body"
        body = m.group(0)
        # The literals 21 and 14 should NOT appear next to `days >`
        assert "days > 21" not in body, "Inline 21 in days> comparison"
        assert "days > 14" not in body, "Inline 14 in days> comparison"
        assert "MEETING_CRITICAL_DAYS" in body
        assert "MEETING_WARNING_DAYS" in body


# ---------------------------------------------------------------------------
# L11 — _exec_returning_id strips trailing whitespace/semicolons
# ---------------------------------------------------------------------------

def test_exec_returning_id_handles_trailing_semicolon():
    """A caller SQL with trailing `;` or whitespace must still produce a
    valid `... RETURNING id` query on Postgres. We can't run PG locally,
    but we exercise the cleanup logic via _q + the rstrip chain."""
    sql = "INSERT INTO foo (x) VALUES (?)  ;  "
    cleaned = sql.rstrip().rstrip(";").rstrip()
    assert cleaned == "INSERT INTO foo (x) VALUES (?)"
    # Source-level guard: the rstrip chain is present in the implementation.
    src = (ROOT / "database.py").read_text()
    m = re.search(r"def _exec_returning_id.*?def ", src, re.DOTALL)
    assert m, "Could not find _exec_returning_id"
    body = m.group(0)
    assert ".rstrip()" in body and 'rstrip(";")' in body, \
        "L11: _exec_returning_id must strip trailing whitespace and semicolons"


# ---------------------------------------------------------------------------
# L12 — security-headers documentation
# ---------------------------------------------------------------------------

def test_streamlit_config_documents_security_headers():
    """The deployment-side config documents the expected proxy-level
    headers operators should add (HSTS, X-Frame-Options, CSP, etc)."""
    cfg = (ROOT / ".streamlit" / "config.toml").read_text()
    for header in (
        "Strict-Transport-Security",
        "X-Frame-Options",
        "X-Content-Type-Options",
        "Content-Security-Policy",
    ):
        assert header in cfg, f"Missing reference to {header}"

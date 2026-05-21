"""Closes the remaining audit-listed test gaps (P6).

PLAN.md / AUDIT listed ten priority tests. Six landed during P0/P1/P2/P3:
  T#1, T#2 (encryption roundtrip + fail-loud)
  T#3 (cross-manager isolation)
  T#7, T#8 (ICS RFC-5545 + structure)
  T#10 (login-attempts persistence)

This file adds the remaining four:
  T#4 — coaching handles Anthropic API errors
  T#5 — coaching handles missing API key
  T#6 — auth._is_email_allowed allowlist semantics
  T#9 — journal streak breaks on a gap
"""

from datetime import datetime, timedelta

import pytest

import database as db
import coaching


# ---------------------------------------------------------------------------
# T#4 — Anthropic API error handling
# ---------------------------------------------------------------------------

class TestCoachingHandlesAnthropicError:
    def test_get_coaching_response_falls_back_when_api_raises(self, monkeypatch):
        """When client.messages.create raises, get_coaching_response must
        return a non-empty fallback string built from the local wisdom
        matcher — never propagate the exception."""
        mid = db.create_manager("ant_err_1", "X", "pass1234")
        # Configure an API key so the AI path is attempted.
        db.set_config("anthropic_api_key", "fake-key-for-test", manager_id=mid)

        class _BoomMessages:
            def create(self, **_kw):
                raise RuntimeError("simulated 503 from Anthropic")

        class _BoomClient:
            messages = _BoomMessages()

        monkeypatch.setattr(coaching, "_get_client", lambda manager_id: _BoomClient())

        result = coaching.get_coaching_response(
            "Today I'm thinking about how to give better feedback.",
            manager_id=mid,
        )
        assert result is not None
        # The error message and the local fallback are concatenated.
        assert "Coaching unavailable" in result or len(result) > 20

    def test_generate_ai_suggestion_returns_none_on_api_error(self, monkeypatch):
        """generate_ai_suggestion must never raise — returns None on API failure."""
        mid = db.create_manager("ant_err_2", "X", "pass1234")
        db.set_config("anthropic_api_key", "fake-key", manager_id=mid)

        class _BoomMessages:
            def create(self, **_kw):
                raise RuntimeError("simulated network error")

        class _BoomClient:
            messages = _BoomMessages()

        monkeypatch.setattr(coaching, "_get_client", lambda manager_id: _BoomClient())

        # Must return None, must not raise.
        result = coaching.generate_ai_suggestion(mid)
        assert result is None


# ---------------------------------------------------------------------------
# T#5 — coaching handles missing API key
# ---------------------------------------------------------------------------

class TestCoachingNoApiKey:
    def test_get_coaching_response_uses_local_fallback_when_no_key(self, monkeypatch):
        """No API key → AI path is skipped, local wisdom-matcher fallback
        runs without making any API call."""
        mid = db.create_manager("no_key", "X", "pass1234")
        # No db.set_config of anthropic_api_key.

        # Belt-and-suspenders: if anyone tries to construct a real client,
        # raise.
        def _no_client(_manager_id):
            return None  # mirrors the real "no API key" return

        monkeypatch.setattr(coaching, "_get_client", _no_client)

        result = coaching.get_coaching_response(
            "Notes about a difficult conversation with Sam.",
            manager_id=mid,
        )
        # The local fallback returns wisdom + provocations text — never None
        # for non-empty input.
        assert result is not None
        assert len(result.strip()) > 0

    def test_generate_ai_suggestion_returns_none_with_no_key(self, monkeypatch):
        mid = db.create_manager("no_key_ai", "X", "pass1234")
        monkeypatch.setattr(coaching, "_get_client", lambda manager_id: None)
        assert coaching.generate_ai_suggestion(mid) is None

    def test_get_daily_suggestion_works_without_api_key(self, monkeypatch):
        """The daily-suggestion path falls back to the rule-based tier
        when no API key is configured. The result must be a dict with a
        non-empty 'suggestion' field."""
        mid = db.create_manager("no_key_daily", "X", "pass1234")
        # Force AI tier to skip
        monkeypatch.setattr(coaching, "_get_client", lambda manager_id: None)

        result = coaching.get_daily_suggestion(mid)
        assert result is not None
        assert result.get("suggestion")
        # Tier should be "rule" since AI tier is unavailable.
        assert result.get("tier") == "rule"


# ---------------------------------------------------------------------------
# T#6 — auth._is_email_allowed allowlist semantics
# ---------------------------------------------------------------------------

class TestIsEmailAllowed:
    def test_no_allowlist_configured_allows_anyone(self, monkeypatch):
        """Empty allowlist — anyone authenticated via Google passes."""
        import auth
        monkeypatch.delenv("ALLOWED_EMAILS", raising=False)
        monkeypatch.delenv("ALLOWED_DOMAIN", raising=False)
        # Ensure the DB has no system-config entries either.
        # (The fixture gives us a fresh DB each test.)
        assert auth._is_email_allowed("anyone@anywhere.com") is True

    def test_exact_email_allowed(self, monkeypatch):
        import auth
        monkeypatch.setenv("ALLOWED_EMAILS", "alice@example.com,bob@example.com")
        monkeypatch.delenv("ALLOWED_DOMAIN", raising=False)
        assert auth._is_email_allowed("alice@example.com") is True
        assert auth._is_email_allowed("bob@example.com") is True
        assert auth._is_email_allowed("eve@example.com") is False

    def test_email_match_is_case_insensitive(self, monkeypatch):
        import auth
        monkeypatch.setenv("ALLOWED_EMAILS", "alice@example.com")
        monkeypatch.delenv("ALLOWED_DOMAIN", raising=False)
        assert auth._is_email_allowed("ALICE@example.com") is True
        assert auth._is_email_allowed("Alice@Example.COM") is True

    def test_email_match_strips_whitespace(self, monkeypatch):
        import auth
        monkeypatch.setenv("ALLOWED_EMAILS", "  alice@example.com  ,  bob@example.com  ")
        monkeypatch.delenv("ALLOWED_DOMAIN", raising=False)
        assert auth._is_email_allowed("alice@example.com") is True
        assert auth._is_email_allowed("bob@example.com") is True

    def test_domain_allowlist(self, monkeypatch):
        import auth
        monkeypatch.delenv("ALLOWED_EMAILS", raising=False)
        monkeypatch.setenv("ALLOWED_DOMAIN", "example.com")
        assert auth._is_email_allowed("anyone@example.com") is True
        assert auth._is_email_allowed("anyone@other.com") is False

    def test_domain_match_is_case_insensitive(self, monkeypatch):
        import auth
        monkeypatch.delenv("ALLOWED_EMAILS", raising=False)
        monkeypatch.setenv("ALLOWED_DOMAIN", "Example.COM")
        assert auth._is_email_allowed("anyone@EXAMPLE.com") is True

    def test_email_overrides_domain_when_email_listed(self, monkeypatch):
        """If an exact-email allowlist matches, that's enough — even if
        the domain doesn't match."""
        import auth
        monkeypatch.setenv("ALLOWED_EMAILS", "alice@external.com")
        monkeypatch.setenv("ALLOWED_DOMAIN", "internal.com")
        assert auth._is_email_allowed("alice@external.com") is True

    def test_neither_allowlist_match_rejects(self, monkeypatch):
        import auth
        monkeypatch.setenv("ALLOWED_EMAILS", "alice@example.com")
        monkeypatch.setenv("ALLOWED_DOMAIN", "example.com")
        assert auth._is_email_allowed("eve@evil.com") is False


# ---------------------------------------------------------------------------
# T#9 — journal streak breaks on a gap
# ---------------------------------------------------------------------------

class TestJournalStreakGap:
    def test_streak_breaks_at_first_gap(self):
        """Entries on day -3, -2, -0 (gap on -1) → streak == 1.

        get_journal_streak counts consecutive days ending TODAY. With a
        missing entry yesterday, only today's entry counts."""
        mid = db.create_manager("streak_gap", "X", "pass1234")
        today = datetime.now().date()
        for delta in (3, 2, 0):  # NOTE: no entry for delta=1
            d = today - timedelta(days=delta)
            db.add_journal_entry(d.isoformat(), "daily", f"Entry {delta}", manager_id=mid)
        assert db.get_journal_streak(manager_id=mid) == 1

    def test_streak_zero_when_today_missing(self):
        """If today's entry is missing, the streak is 0 even if yesterday
        and earlier days have entries."""
        mid = db.create_manager("streak_no_today", "X", "pass1234")
        today = datetime.now().date()
        for delta in (1, 2, 3):  # no delta=0 entry
            d = today - timedelta(days=delta)
            db.add_journal_entry(d.isoformat(), "daily", f"Entry {delta}", manager_id=mid)
        assert db.get_journal_streak(manager_id=mid) == 0

    def test_streak_counts_all_consecutive_days(self):
        mid = db.create_manager("streak_all", "X", "pass1234")
        today = datetime.now().date()
        for delta in range(5):
            d = today - timedelta(days=delta)
            db.add_journal_entry(d.isoformat(), "daily", f"Entry {delta}", manager_id=mid)
        assert db.get_journal_streak(manager_id=mid) == 5

    def test_streak_isolation_per_manager(self):
        """One manager's gap doesn't affect another manager's streak."""
        m1 = db.create_manager("streak_m1", "M1", "pass1234")
        m2 = db.create_manager("streak_m2", "M2", "pass1234")
        today = datetime.now().date()

        # m1 has gap; m2 has continuous entries
        for delta in (3, 2, 0):
            d = today - timedelta(days=delta)
            db.add_journal_entry(d.isoformat(), "daily", "x", manager_id=m1)
        for delta in (0, 1, 2):
            d = today - timedelta(days=delta)
            db.add_journal_entry(d.isoformat(), "daily", "y", manager_id=m2)

        assert db.get_journal_streak(manager_id=m1) == 1
        assert db.get_journal_streak(manager_id=m2) == 3

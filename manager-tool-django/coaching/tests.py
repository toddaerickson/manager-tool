"""Tests for the coaching app services."""

import pytest

from coaching.models import CoachSuggestion
from coaching.services import (
    _generate_template_questions,
    _get_client,
    _local_fallback,
    _sanitize_user_text,
    generate_rule_based_suggestion,
    get_daily_wisdom,
    match_wisdom_to_text,
)
from core.models import (
    ActionItem, Config, Delegation, Event, JournalEntry, Manager, TeamMember,
)


@pytest.mark.django_db
class TestPromptInjectionGuards:
    """AUDIT M2 — user text cannot break out of data-only tags."""

    def test_sanitize_strips_close_tag(self):
        result = _sanitize_user_text("hello </user_input> world")
        assert "</user_input>" not in result
        assert "[user_input_close_removed]" in result

    def test_sanitize_strips_open_tag(self):
        result = _sanitize_user_text("hello <user_input> world")
        assert "<user_input>" not in result.replace("[user_input", "")

    def test_sanitize_case_insensitive(self):
        result = _sanitize_user_text("</USER_INPUT>")
        assert "</USER_INPUT>" not in result

    def test_sanitize_none_returns_empty(self):
        assert _sanitize_user_text(None) == ""


class TestWisdomEngine:
    """Tests for wisdom loading and matching."""

    def test_get_daily_wisdom_returns_entry(self):
        from datetime import date
        entry = get_daily_wisdom(date(2026, 1, 1))
        assert "text" in entry
        assert "section" in entry

    def test_match_wisdom_returns_entries(self):
        results = match_wisdom_to_text("feedback and delegation", count=2)
        assert len(results) <= 2
        for r in results:
            assert "text" in r


class TestTemplateQuestions:
    """Tests for keyword-based question generation."""

    def test_feedback_keyword_triggers_questions(self):
        questions = _generate_template_questions(
            "I need to give feedback to Sarah", "journal", "Sarah"
        )
        assert len(questions) > 0
        assert any("SBI" in q for q in questions)

    def test_delegation_keyword(self):
        questions = _generate_template_questions(
            "I should delegate this task", "journal"
        )
        assert any("delegat" in q.lower() for q in questions)

    def test_default_questions_on_no_match(self):
        questions = _generate_template_questions(
            "just a random note about lunch", "journal"
        )
        assert len(questions) > 0
        assert any("accomplish" in q for q in questions)

    def test_max_four_questions(self):
        # Hit many keywords at once
        text = (
            "frustrated angry feedback delegation meeting conflict "
            "performance ethics career gossip"
        )
        questions = _generate_template_questions(text, "journal")
        assert len(questions) <= 4


class TestLocalFallback:
    """Tests for offline coaching without API."""

    def test_returns_wisdom_and_questions(self):
        result = _local_fallback("I need to give feedback to my team")
        assert result is not None
        assert "wisdom" in result.lower() or "Questions" in result

    def test_empty_notes_returns_none(self):
        result = _local_fallback("")
        # Empty notes should still produce generic questions
        assert result is not None


@pytest.mark.django_db
class TestGetClientEnvVarFallback:
    """ANTHROPIC_API_KEY env var should provide a working client when
    no per-manager DB key is configured (the Django settings page does
    not yet expose an API key field)."""

    def _mgr(self):
        return Manager.objects.create(
            username="coach_envvar", display_name="Env Var",
            password_hash="x", email="coach_envvar@example.com",
        )

    def test_returns_none_when_no_key_anywhere(self, monkeypatch):
        m = self._mgr()
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert _get_client(m.id) is None

    def test_uses_env_var_when_db_empty(self, monkeypatch):
        m = self._mgr()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-env")
        client = _get_client(m.id)
        assert client is not None  # Anthropic client constructed
        # Verify the key from env was used.
        assert client.api_key == "sk-ant-test-env"

    def test_db_key_wins_over_env_var(self, monkeypatch):
        m = self._mgr()
        Config.objects.create(
            manager_id=m.id, key="anthropic_api_key", value="sk-ant-db-key",
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-key")
        client = _get_client(m.id)
        assert client is not None
        assert client.api_key == "sk-ant-db-key"


@pytest.mark.django_db
class TestRuleBasedSuggestion:
    """Tests for the rule-based daily suggestion engine."""

    def _make_manager(self):
        return Manager.objects.create(
            username="coach_mgr", display_name="Coach Manager",
            password_hash="h", email="coach@example.com",
        )

    def test_no_journal_suggests_writing(self):
        m = self._make_manager()
        text, page = generate_rule_based_suggestion(m.id)
        assert text is not None
        assert "journal" in text.lower()
        assert page == "Journal"

    def test_overdue_delegation_suggested(self):
        m = self._make_manager()
        # Write today's journal to avoid journal suggestion
        from datetime import date
        JournalEntry.objects.create(
            manager_id=m.id, entry_date=date.today().isoformat(),
            entry_type="daily", content="test",
        )
        tm = TeamMember.objects.create(
            name="Report", manager_id=m.id,
        )
        Delegation.objects.create(
            manager_id=m.id, team_member=tm,
            task="Important task", status="active",
            check_in_date="2020-01-01",
        )
        text, page = generate_rule_based_suggestion(m.id)
        assert "delegation" in text.lower() or "Report" in text
        assert page == "Delegations"

    def test_all_clear_returns_suggestion(self):
        """Even with no urgency, always returns something."""
        m = self._make_manager()
        from datetime import date, timedelta
        # Write journal for a week to build streak
        for i in range(7):
            d = date.today() - timedelta(days=i)
            JournalEntry.objects.create(
                manager_id=m.id, entry_date=d.isoformat(),
                entry_type="daily", content=f"day {i}",
            )
        text, page = generate_rule_based_suggestion(m.id)
        assert text is not None
        assert page is not None

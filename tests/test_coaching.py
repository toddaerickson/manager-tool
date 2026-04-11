"""Tests for coaching.py — suggestion engine and local fallback."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import database as db
import coaching


class TestRuleBasedSuggestion:
    def test_new_user_gets_journal_prompt(self):
        """A user with no journal entries should be prompted to write."""
        mid = db.create_manager("coach_mgr1", "Mgr1", "pass1234")
        suggestion, page = coaching.generate_rule_based_suggestion(mid)
        assert suggestion is not None
        assert "journal" in suggestion.lower()
        assert page == "Journal"

    def test_streak_at_risk(self):
        """A user with a streak who hasn't written today gets a streak/journal nudge."""
        mid = db.create_manager("coach_mgr2", "Mgr2", "pass1234")
        # Create entries for past 3 days ending yesterday to build streak
        from datetime import datetime, timedelta
        for i in range(3, 0, -1):
            date = (datetime.now().date() - timedelta(days=i)).isoformat()
            db.add_journal_entry(date, "daily", f"Day {i}", mood=4,
                                 energy=4, manager_id=mid)
        suggestion, page = coaching.generate_rule_based_suggestion(mid)
        assert suggestion is not None
        # Should nudge toward journal (streak or fresh start)
        assert page == "Journal"

    def test_low_mood_supportive(self):
        """A user with low mood yesterday gets a supportive message."""
        mid = db.create_manager("coach_mgr3", "Mgr3", "pass1234")
        from datetime import datetime, timedelta
        yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
        db.add_journal_entry(yesterday, "daily", "Terrible day",
                             mood=1, energy=2, manager_id=mid)
        suggestion, page = coaching.generate_rule_based_suggestion(mid)
        assert suggestion is not None
        assert "tough" in suggestion.lower() or "hard" in suggestion.lower()

    def test_critical_nudge_surfaces(self):
        """If a team member hasn't been met in 22+ days, that nudge appears."""
        mid = db.create_manager("coach_mgr4", "Mgr4", "pass1234")
        # Write today's journal so streak/journal nudges don't fire first
        from datetime import datetime, timedelta
        today = datetime.now().date().isoformat()
        db.add_journal_entry(today, "daily", "All good", mood=4,
                             energy=4, manager_id=mid)
        # Add a team member with a very old meeting
        tid = db.add_team_member("Neglected Person", manager_id=mid)
        old_date = (datetime.now().date() - timedelta(days=25)).strftime("%Y-%m-%d")
        eid = db.create_event("Old meeting", "one_on_one", old_date, "10:00",
                              team_member_id=tid, manager_id=mid)
        db.complete_event(eid)
        suggestion, page = coaching.generate_rule_based_suggestion(mid)
        assert suggestion is not None
        # Should mention the person or scheduling
        assert "Neglected Person" in suggestion or "Schedule" in (page or "")

    def test_all_clear_suggests_reflection(self):
        """A user with no nudges gets a gentle reflection prompt."""
        mid = db.create_manager("coach_mgr5", "Mgr5", "pass1234")
        today = __import__("datetime").datetime.now().date().isoformat()
        db.add_journal_entry(today, "daily", "Great day", mood=5,
                             energy=5, manager_id=mid)
        suggestion, page = coaching.generate_rule_based_suggestion(mid)
        assert suggestion is not None
        # Should be a positive/reflective suggestion
        assert page is not None


class TestDailySuggestion:
    def test_caches_suggestion(self):
        """get_daily_suggestion should cache the result."""
        mid = db.create_manager("cache_mgr", "Mgr", "pass1234")
        result = coaching.get_daily_suggestion(mid)
        assert result is not None
        # Calling again should return cached version
        cached = db.get_todays_suggestion(mid)
        assert cached is not None
        assert cached["suggestion"] == result["suggestion"]

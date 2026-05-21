"""Tests for templates.py — wisdom engine, agenda generation, anti-patterns."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import templates


class TestAgendaGeneration:
    def test_check_in_agenda(self):
        agenda = templates.generate_agenda("check_in", "Sarah")
        assert "Sarah" in agenda
        assert "WEEKLY CHECK-IN" in agenda

    def test_coaching_agenda(self):
        agenda = templates.generate_agenda("coaching", "Bob")
        assert "GROW" in agenda
        assert "Bob" in agenda

    def test_one_on_one_agenda(self):
        agenda = templates.generate_agenda("one_on_one")
        assert "1-on-1" in agenda or "1-ON-1" in agenda

    def test_quarterly_review_agenda(self):
        agenda = templates.generate_agenda("quarterly_review", "Alice")
        assert "Alice" in agenda

    def test_unknown_type(self):
        agenda = templates.generate_agenda("nonexistent")
        assert "No template" in agenda


class TestEventTypes:
    def test_all_types_exist(self):
        expected = {"check_in", "coaching", "one_on_one", "quarterly_review", "other"}
        assert set(templates.EVENT_TYPES.keys()) == expected

    def test_default_title(self):
        title = templates.get_default_title("coaching", "Alice")
        assert "Alice" in title
        assert "Coaching" in title


class TestAntiPatterns:
    def test_ghost_detection(self):
        meeting_data = [{"member_name": "Sarah", "member_id": 1, "days_since": 25}]
        patterns = templates.detect_anti_patterns(meeting_data, [])
        assert any(p["pattern"] == "The Ghost" for p in patterns)

    def test_no_patterns_when_healthy(self):
        meeting_data = [{"member_name": "Sarah", "member_id": 1, "days_since": 5}]
        feedback_data = [{"member_name": "Sarah", "positive_count": 5,
                         "constructive_count": 1, "total_count": 6}]
        patterns = templates.detect_anti_patterns(meeting_data, feedback_data)
        assert len(patterns) == 0

    def test_micromanager_detection(self):
        feedback_data = [{"member_name": "Sarah", "positive_count": 0,
                         "constructive_count": 5, "total_count": 5}]
        patterns = templates.detect_anti_patterns([], feedback_data)
        assert any(p["pattern"] == "The Micromanager" for p in patterns)


class TestTips:
    def test_random_tip(self):
        tip = templates.get_random_tip()
        assert isinstance(tip, str)
        assert len(tip) > 0

    def test_tips_by_count(self):
        tips = templates.get_tips_by_count(5)
        assert len(tips) == 5


class TestWisdom:
    def test_daily_wisdom(self):
        wisdom = templates.get_daily_wisdom()
        assert "text" in wisdom
        assert "number" in wisdom

    def test_match_wisdom(self):
        results = templates.match_wisdom_to_text("delegation trust", count=2)
        assert len(results) >= 1
        assert "text" in results[0]

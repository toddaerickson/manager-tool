"""Tests for database.py — CRUD operations, multi-tenancy, and helpers."""

import database as db


class TestPlaceholderConversion:
    def test_q_sqlite(self):
        """SQLite mode should leave ? placeholders unchanged."""
        assert db._q("SELECT * FROM t WHERE id = ?") == "SELECT * FROM t WHERE id = ?"

    def test_q_conversion(self, monkeypatch):
        """PostgreSQL mode should convert ? to %s."""
        monkeypatch.setattr(db, "_USE_PG", True)
        assert db._q("SELECT * WHERE a = ? AND b = ?") == "SELECT * WHERE a = %s AND b = %s"
        monkeypatch.setattr(db, "_USE_PG", False)


class TestManagerAuth:
    def test_create_and_authenticate(self):
        mid = db.create_manager("alice", "Alice A", "password123")
        assert mid is not None

        manager = db.authenticate_manager("alice", "password123")
        assert manager is not None
        assert manager["display_name"] == "Alice A"

    def test_wrong_password(self):
        db.create_manager("bob", "Bob B", "correct_pw")
        assert db.authenticate_manager("bob", "wrong_pw") is None

    def test_bcrypt_hash_not_sha256(self):
        """New accounts should use bcrypt, not 64-char SHA-256."""
        mid = db.create_manager("charlie", "Charlie C", "mypassword")
        manager = db.get_manager(mid)
        # bcrypt hashes start with $2b$ and are ~60 chars
        assert manager["password_hash"].startswith("$2b$")
        assert len(manager["password_hash"]) > 50

    def test_manager_exists(self):
        db.create_manager("dave", "Dave D", "pw123456")
        assert db.manager_exists("dave") is True
        assert db.manager_exists("nonexistent") is False

    def test_update_password(self):
        mid = db.create_manager("eve", "Eve E", "oldpass99")
        db.update_manager_password(mid, "newpass99")
        assert db.authenticate_manager("eve", "newpass99") is not None
        assert db.authenticate_manager("eve", "oldpass99") is None


class TestMultiTenancy:
    def _create_two_managers(self):
        m1 = db.create_manager("manager1", "Manager One", "pass1234")
        m2 = db.create_manager("manager2", "Manager Two", "pass1234")
        return m1, m2

    def test_team_members_isolated(self):
        m1, m2 = self._create_two_managers()
        db.add_team_member("Alice", manager_id=m1)
        db.add_team_member("Bob", manager_id=m2)

        members_m1 = db.list_team_members(manager_id=m1)
        members_m2 = db.list_team_members(manager_id=m2)

        assert len(members_m1) == 1
        assert members_m1[0]["name"] == "Alice"
        assert len(members_m2) == 1
        assert members_m2[0]["name"] == "Bob"

    def test_events_isolated(self):
        m1, m2 = self._create_two_managers()
        db.create_event("M1 Meeting", "check_in", "2025-01-15", "10:00",
                        manager_id=m1)
        db.create_event("M2 Meeting", "check_in", "2025-01-15", "11:00",
                        manager_id=m2)

        events_m1 = db.list_events(manager_id=m1)
        events_m2 = db.list_events(manager_id=m2)

        assert len(events_m1) == 1
        assert events_m1[0]["title"] == "M1 Meeting"
        assert len(events_m2) == 1
        assert events_m2[0]["title"] == "M2 Meeting"

    def test_journal_isolated(self):
        m1, m2 = self._create_two_managers()
        db.add_journal_entry("2025-01-15", "daily", "M1 entry", manager_id=m1)
        db.add_journal_entry("2025-01-15", "daily", "M2 entry", manager_id=m2)

        j1 = db.list_journal_entries(manager_id=m1)
        j2 = db.list_journal_entries(manager_id=m2)

        assert len(j1) == 1
        assert j1[0]["content"] == "M1 entry"
        assert len(j2) == 1
        assert j2[0]["content"] == "M2 entry"

    def test_action_items_isolated(self):
        m1, m2 = self._create_two_managers()
        db.add_action_item("Task for M1", manager_id=m1)
        db.add_action_item("Task for M2", manager_id=m2)

        a1 = db.get_pending_action_items(manager_id=m1)
        a2 = db.get_pending_action_items(manager_id=m2)

        assert len(a1) == 1
        assert a1[0]["description"] == "Task for M1"
        assert len(a2) == 1

    def test_no_filter_returns_all(self):
        """When manager_id is None, all data is returned (backward compat)."""
        m1, m2 = self._create_two_managers()
        db.add_team_member("Alice", manager_id=m1)
        db.add_team_member("Bob", manager_id=m2)

        all_members = db.list_team_members()
        assert len(all_members) == 2


class TestTeamMembers:
    def test_add_and_list(self):
        mid = db.create_manager("mgr", "Mgr", "pass1234")
        tid = db.add_team_member("Sarah", email="sarah@test.com",
                                  role="Engineer", manager_id=mid)
        assert tid is not None

        members = db.list_team_members(manager_id=mid)
        assert len(members) == 1
        assert members[0]["email"] == "sarah@test.com"

    def test_get_by_name(self):
        mid = db.create_manager("mgr2", "Mgr2", "pass1234")
        db.add_team_member("John Smith", manager_id=mid)

        member = db.get_team_member_by_name("john smith", manager_id=mid)
        assert member is not None
        assert member["name"] == "John Smith"

    def test_delete(self):
        mid = db.create_manager("mgr3", "Mgr3", "pass1234")
        tid = db.add_team_member("ToDelete", manager_id=mid)
        db.delete_team_member(tid, manager_id=mid)

        members = db.list_team_members(manager_id=mid)
        assert len(members) == 0


class TestEvents:
    def test_create_and_complete(self):
        mid = db.create_manager("mgr", "Mgr", "pass1234")
        eid = db.create_event("Weekly 1:1", "one_on_one", "2025-01-20", "10:00",
                              manager_id=mid)
        assert eid is not None

        db.complete_event(eid, notes="Great meeting")
        event = db.get_event(eid)
        assert event["status"] == "completed"
        assert event["notes"] == "Great meeting"

    def test_upcoming_events(self):
        from datetime import datetime, timedelta
        mid = db.create_manager("mgr2", "Mgr2", "pass1234")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        db.create_event("Tomorrow Meeting", "check_in", tomorrow, "10:00",
                        manager_id=mid)

        upcoming = db.get_upcoming_events(days=7, manager_id=mid)
        assert len(upcoming) >= 1


class TestConfig:
    def test_set_and_get(self):
        db.set_config("test_key", "test_value")
        assert db.get_config("test_key") == "test_value"

    def test_default_value(self):
        assert db.get_config("nonexistent", default="fallback") == "fallback"

    def test_upsert(self):
        db.set_config("changing", "v1")
        assert db.get_config("changing") == "v1"
        db.set_config("changing", "v2")
        assert db.get_config("changing") == "v2"


class TestJournal:
    def test_add_and_retrieve(self):
        mid = db.create_manager("mgr", "Mgr", "pass1234")
        eid = db.add_journal_entry("2025-01-15", "daily", "Good day",
                                   mood=4, energy=3, manager_id=mid)
        assert eid is not None

        entry = db.get_journal_entry_by_date("2025-01-15", "daily", manager_id=mid)
        assert entry is not None
        assert entry["content"] == "Good day"
        assert entry["mood"] == 4

    def test_streak_empty(self):
        mid = db.create_manager("mgr2", "Mgr2", "pass1234")
        assert db.get_journal_streak(manager_id=mid) == 0

    def test_update_with_coaching_response(self):
        mid = db.create_manager("mgr3", "Mgr3", "pass1234")
        eid = db.add_journal_entry("2025-01-15", "daily", "Test entry",
                                   manager_id=mid)
        db.update_journal_entry(eid, coaching_response="Great insight!")

        entry = db.get_journal_entry_by_date("2025-01-15", "daily", manager_id=mid)
        assert entry["coaching_response"] == "Great insight!"


class TestFeedbackAndGoals:
    def test_add_feedback(self):
        mid = db.create_manager("mgr", "Mgr", "pass1234")
        tid = db.add_team_member("Sarah", manager_id=mid)
        fid = db.add_feedback(tid, "positive", "In standup",
                              "Clear explanation", "Team understood")
        assert fid is not None

        feedback = db.list_feedback(team_member_id=tid)
        assert len(feedback) == 1
        assert feedback[0]["feedback_type"] == "positive"

    def test_add_goal(self):
        mid = db.create_manager("mgr2", "Mgr2", "pass1234")
        tid = db.add_team_member("Bob", manager_id=mid)
        gid = db.add_goal(tid, "Q1 2025", "Ship v2.0")
        assert gid is not None

        goals = db.list_goals(team_member_id=tid)
        assert len(goals) == 1
        assert goals[0]["description"] == "Ship v2.0"


class TestDelegations:
    def test_add_and_list(self):
        mid = db.create_manager("mgr", "Mgr", "pass1234")
        tid = db.add_team_member("Sarah", manager_id=mid)
        did = db.add_delegation(
            task="Lead sprint planning", team_member_id=tid,
            outcome_expected="Team has clear priorities",
            autonomy_level="guided", manager_id=mid)
        assert did is not None

        delegations = db.list_delegations(manager_id=mid)
        assert len(delegations) == 1
        assert delegations[0]["task"] == "Lead sprint planning"
        assert delegations[0]["member_name"] == "Sarah"

    def test_complete_delegation(self):
        mid = db.create_manager("mgr2", "Mgr2", "pass1234")
        did = db.add_delegation(task="Write docs", manager_id=mid)
        db.update_delegation(did, status="completed")

        active = db.list_delegations(manager_id=mid, status="active")
        assert len(active) == 0

    def test_isolation(self):
        m1 = db.create_manager("del_m1", "M1", "pass1234")
        m2 = db.create_manager("del_m2", "M2", "pass1234")
        db.add_delegation(task="M1 task", manager_id=m1)
        db.add_delegation(task="M2 task", manager_id=m2)

        assert len(db.list_delegations(manager_id=m1)) == 1
        assert len(db.list_delegations(manager_id=m2)) == 1

    def test_active_count(self):
        mid = db.create_manager("mgr3", "Mgr3", "pass1234")
        db.add_delegation(task="Task 1", manager_id=mid)
        db.add_delegation(task="Task 2", manager_id=mid)
        assert db.get_active_delegations_count(manager_id=mid) == 2


class TestRunningNotes:
    def test_add_and_list(self):
        mid = db.create_manager("mgr", "Mgr", "pass1234")
        tid = db.add_team_member("Alice", manager_id=mid)
        nid = db.add_running_note(
            team_member_id=tid, content="Great presentation today",
            category="praise", manager_id=mid)
        assert nid is not None

        notes = db.list_running_notes(tid, manager_id=mid)
        assert len(notes) == 1
        assert notes[0]["content"] == "Great presentation today"
        assert notes[0]["category"] == "praise"

    def test_ordering(self):
        mid = db.create_manager("mgr2", "Mgr2", "pass1234")
        tid = db.add_team_member("Bob", manager_id=mid)
        db.add_running_note(tid, "Old note", note_date="2025-01-01", manager_id=mid)
        db.add_running_note(tid, "New note", note_date="2025-01-15", manager_id=mid)

        notes = db.list_running_notes(tid, manager_id=mid)
        assert notes[0]["content"] == "New note"  # newest first

    def test_delete(self):
        mid = db.create_manager("mgr3", "Mgr3", "pass1234")
        tid = db.add_team_member("Charlie", manager_id=mid)
        nid = db.add_running_note(tid, "To delete", manager_id=mid)
        db.delete_running_note(nid)

        notes = db.list_running_notes(tid, manager_id=mid)
        assert len(notes) == 0


class TestDecisionLog:
    def test_add_and_list(self):
        mid = db.create_manager("mgr", "Mgr", "pass1234")
        did = db.add_decision(
            title="Hire a senior engineer",
            context="Team is bottlenecked on backend work",
            rationale="ROI > contractor, long-term investment",
            expected_outcome="Reduce sprint carryover by 50%",
            review_date="2025-06-01",
            manager_id=mid)
        assert did is not None

        decisions = db.list_decisions(manager_id=mid)
        assert len(decisions) == 1
        assert decisions[0]["title"] == "Hire a senior engineer"
        assert decisions[0]["status"] == "active"

    def test_update_with_actual_outcome(self):
        mid = db.create_manager("mgr2", "Mgr2", "pass1234")
        did = db.add_decision(title="Switch to weekly releases", manager_id=mid)
        db.update_decision(did, status="validated",
                          actual_outcome="Reduced deployment risk significantly")

        decisions = db.list_decisions(manager_id=mid)
        assert decisions[0]["status"] == "validated"
        assert "deployment risk" in decisions[0]["actual_outcome"]

    def test_isolation(self):
        m1 = db.create_manager("dec_m1", "M1", "pass1234")
        m2 = db.create_manager("dec_m2", "M2", "pass1234")
        db.add_decision(title="M1 decision", manager_id=m1)
        db.add_decision(title="M2 decision", manager_id=m2)

        assert len(db.list_decisions(manager_id=m1)) == 1
        assert len(db.list_decisions(manager_id=m2)) == 1

    def test_delete(self):
        mid = db.create_manager("mgr3", "Mgr3", "pass1234")
        did = db.add_decision(title="To delete", manager_id=mid)
        db.delete_decision(did)

        assert len(db.list_decisions(manager_id=mid)) == 0

"""Tests for database.py — CRUD operations, multi-tenancy, and helpers."""

from datetime import datetime

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

    def test_update_password_with_correct_old_password(self):
        mid = db.create_manager("eve", "Eve E", "oldpass99")
        db.update_manager_password(mid, "oldpass99", "newpass99")
        assert db.authenticate_manager("eve", "newpass99") is not None
        assert db.authenticate_manager("eve", "oldpass99") is None

    def test_update_password_rejects_wrong_old_password(self):
        """Regression for AUDIT H4 — must verify current password before update."""
        mid = db.create_manager("frank", "Frank F", "realpass99")
        try:
            db.update_manager_password(mid, "wrongpass", "newpass99")
        except db.IncorrectPasswordError:
            pass
        else:
            raise AssertionError(
                "update_manager_password must reject an incorrect old password"
            )
        assert db.authenticate_manager("frank", "realpass99") is not None
        assert db.authenticate_manager("frank", "newpass99") is None

    def test_update_password_rejects_unknown_manager_id(self):
        try:
            db.update_manager_password(999999, "anything", "newpass99")
        except db.IncorrectPasswordError:
            return
        raise AssertionError(
            "update_manager_password must reject an unknown manager_id"
        )


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

        db.complete_event(eid, manager_id=mid, notes="Great meeting")
        event = db.get_event(eid, manager_id=mid)
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
        mid = db.create_manager("cfg_basic", "Cfg", "pass1234")
        db.set_config("test_key", "test_value", manager_id=mid)
        assert db.get_config("test_key", manager_id=mid) == "test_value"

    def test_default_value(self):
        mid = db.create_manager("cfg_default", "Cfg", "pass1234")
        assert db.get_config("nonexistent", manager_id=mid, default="fallback") == "fallback"

    def test_upsert(self):
        mid = db.create_manager("cfg_upsert", "Cfg", "pass1234")
        db.set_config("changing", "v1", manager_id=mid)
        assert db.get_config("changing", manager_id=mid) == "v1"
        db.set_config("changing", "v2", manager_id=mid)
        assert db.get_config("changing", manager_id=mid) == "v2"


class TestPerTenantConfig:
    """Regression for AUDIT C3 / P1.3 — config rows must be scoped per
    manager_id; system keys live under SYSTEM_MANAGER_ID."""

    def test_per_tenant_isolation(self):
        m1 = db.create_manager("ptc_m1", "M1", "pass1234")
        m2 = db.create_manager("ptc_m2", "M2", "pass1234")
        db.set_config("anthropic_api_key", "M1 key", manager_id=m1)
        db.set_config("anthropic_api_key", "M2 key", manager_id=m2)

        assert db.get_config("anthropic_api_key", manager_id=m1) == "M1 key"
        assert db.get_config("anthropic_api_key", manager_id=m2) == "M2 key"
        # Explicit cross-tenant negative: m2 must NEVER see m1's key.
        assert db.get_config("anthropic_api_key", manager_id=m2) != "M1 key"
        assert db.get_config("anthropic_api_key", manager_id=m1) != "M2 key"

    def test_per_tenant_isolation_one_sided(self):
        """Only m1 sets a key; m2 must read None, not m1's value."""
        m1 = db.create_manager("ptc_only_m1", "M1", "pass1234")
        m2 = db.create_manager("ptc_only_m2", "M2", "pass1234")
        db.set_config("anthropic_api_key", "M1 key", manager_id=m1)
        assert db.get_config("anthropic_api_key", manager_id=m2) is None

    def test_get_all_config_scoped(self):
        m1 = db.create_manager("ptc_all_m1", "M1", "pass1234")
        m2 = db.create_manager("ptc_all_m2", "M2", "pass1234")
        db.set_config("manager_name", "Alice", manager_id=m1)
        db.set_config("manager_name", "Bob", manager_id=m2)

        m1_cfg = db.get_all_config(manager_id=m1)
        m2_cfg = db.get_all_config(manager_id=m2)
        assert m1_cfg.get("manager_name") == "Alice"
        assert m2_cfg.get("manager_name") == "Bob"
        # No cross-leakage
        assert "manager_name" in m1_cfg
        assert m1_cfg["manager_name"] != "Bob"

    def test_system_config_separate_from_tenant(self):
        m1 = db.create_manager("ptc_sys_m1", "M1", "pass1234")
        db.set_config("google_client_id", "system-cid", manager_id=db.SYSTEM_MANAGER_ID)
        # Manager m1's config does NOT include the system row
        assert db.get_config("google_client_id", manager_id=m1) is None
        assert db.get_config("google_client_id", manager_id=db.SYSTEM_MANAGER_ID) == "system-cid"

    def test_is_system_key(self):
        assert db._is_system_key("google_client_id") is True
        assert db._is_system_key("oauth_redirect_uri") is True
        assert db._is_system_key("_migration_backfill_done") is True
        assert db._is_system_key("anthropic_api_key") is False
        assert db._is_system_key("manager_name") is False


class TestSensitiveConfigEncryption:
    """Regression for AUDIT C4 — sensitive config must encrypt at rest and
    fail loud when encryption is unavailable."""

    def test_sensitive_config_roundtrip(self):
        mid = db.create_manager("enc_round", "E", "pass1234")
        secret = "sk-test-1234567890abcdef"
        db.set_config("anthropic_api_key", secret, manager_id=mid)

        assert db.get_config("anthropic_api_key", manager_id=mid) == secret

        raw = db.get_all_config(manager_id=mid)["anthropic_api_key"]
        assert raw.startswith(db._ENC_PREFIX), "Sensitive value must be encrypted at rest"
        assert secret not in raw, "Plaintext secret must not appear in stored value"

    def test_non_sensitive_config_not_encrypted(self):
        mid = db.create_manager("enc_plain", "E", "pass1234")
        db.set_config("manager_name", "Alice", manager_id=mid)
        assert db.get_all_config(manager_id=mid)["manager_name"] == "Alice"

    def test_encryption_unavailable_fails_loud_on_write(self, monkeypatch):
        mid = db.create_manager("enc_failwrite", "E", "pass1234")
        monkeypatch.setattr(db, "_get_fernet", lambda: None)
        try:
            db.set_config("smtp_password", "hunter2", manager_id=mid)
        except db.EncryptionUnavailableError:
            return
        raise AssertionError(
            "set_config must raise EncryptionUnavailableError for sensitive keys "
            "when encryption is not available; instead it silently stored the value"
        )

    def test_decryption_failure_raises(self, monkeypatch):
        mid = db.create_manager("enc_failread", "E", "pass1234")
        secret = "sk-original"
        db.set_config("anthropic_api_key", secret, manager_id=mid)

        monkeypatch.setattr(db, "_get_fernet", lambda: None)
        try:
            db.get_config("anthropic_api_key", manager_id=mid)
        except db.EncryptionUnavailableError:
            return
        raise AssertionError(
            "get_config must raise EncryptionUnavailableError when an encrypted "
            "value cannot be decrypted; instead it returned the ciphertext"
        )


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
        db.update_journal_entry(eid, manager_id=mid, coaching_response="Great insight!")

        entry = db.get_journal_entry_by_date("2025-01-15", "daily", manager_id=mid)
        assert entry["coaching_response"] == "Great insight!"


class TestFeedbackAndGoals:
    def test_add_feedback(self):
        mid = db.create_manager("mgr", "Mgr", "pass1234")
        tid = db.add_team_member("Sarah", manager_id=mid)
        fid = db.add_feedback(tid, "positive", "In standup",
                              "Clear explanation", "Team understood")
        assert fid is not None

        feedback = db.list_feedback(manager_id=mid, team_member_id=tid)
        assert len(feedback) == 1
        assert feedback[0]["feedback_type"] == "positive"

    def test_add_goal(self):
        mid = db.create_manager("mgr2", "Mgr2", "pass1234")
        tid = db.add_team_member("Bob", manager_id=mid)
        gid = db.add_goal(tid, "Q1 2025", "Ship v2.0")
        assert gid is not None

        goals = db.list_goals(manager_id=mid, team_member_id=tid)
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
        did = db.add_delegation(task="Write docs",
                                outcome_expected="Docs published to wiki",
                                manager_id=mid)
        db.update_delegation(did, manager_id=mid, status="completed")

        active = db.list_delegations(manager_id=mid, status="active")
        assert len(active) == 0

    def test_isolation(self):
        m1 = db.create_manager("del_m1", "M1", "pass1234")
        m2 = db.create_manager("del_m2", "M2", "pass1234")
        db.add_delegation(task="M1 task",
                          outcome_expected="M1 result delivered", manager_id=m1)
        db.add_delegation(task="M2 task",
                          outcome_expected="M2 result delivered", manager_id=m2)

        assert len(db.list_delegations(manager_id=m1)) == 1
        assert len(db.list_delegations(manager_id=m2)) == 1

    def test_active_count(self):
        mid = db.create_manager("mgr3", "Mgr3", "pass1234")
        db.add_delegation(task="Task 1",
                          outcome_expected="Task 1 outcome", manager_id=mid)
        db.add_delegation(task="Task 2",
                          outcome_expected="Task 2 outcome", manager_id=mid)
        assert db.get_active_delegations_count(manager_id=mid) == 2


class TestDelegationOutcomeValidation:
    """add_delegation / update_delegation reject delegations without a
    substantive outcome statement. Per the approved stage-of-delivery plan:
    a delegation without a defined outcome is just a to-do in nicer clothes,
    so the writer enforces the discipline server-side."""

    def _mid(self):
        return db.create_manager("dv_mgr", "DV", "pass1234")

    def test_empty_outcome_rejected(self):
        mid = self._mid()
        for bad in (None, "", "   "):
            try:
                db.add_delegation(task="real task", outcome_expected=bad,
                                  manager_id=mid)
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"empty outcome_expected={bad!r} must raise ValueError")

    def test_junk_strings_rejected(self):
        mid = self._mid()
        for bad in ("n/a", "N/A", "  na  ", "TBD", "tbd"):
            try:
                db.add_delegation(task="real task", outcome_expected=bad,
                                  manager_id=mid)
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"junk outcome_expected={bad!r} must raise ValueError")

    def test_outcome_equals_task_rejected(self):
        """Outcome must be DISTINCT from the task — describing the result,
        not the activity. Prevents the LinkedIn-skill-endorsement
        degeneration where the manager just copies the task into the outcome."""
        mid = self._mid()
        try:
            db.add_delegation(task="Run the Q3 review",
                              outcome_expected="Run the Q3 review",
                              manager_id=mid)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "outcome_expected == task must raise ValueError")
        # Case + whitespace-insensitive: should still reject.
        try:
            db.add_delegation(task="Run the Q3 review",
                              outcome_expected="  RUN THE Q3 REVIEW  ",
                              manager_id=mid)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "outcome_expected == task (case/whitespace) must raise ValueError")

    def test_substantive_outcome_accepted(self):
        mid = self._mid()
        did = db.add_delegation(task="Run the Q3 review",
                                outcome_expected="Final report shared with execs",
                                manager_id=mid)
        assert did is not None

    def test_empty_task_rejected(self):
        mid = self._mid()
        try:
            db.add_delegation(task="   ",
                              outcome_expected="something",
                              manager_id=mid)
        except ValueError:
            pass
        else:
            raise AssertionError("empty task must raise ValueError")

    def test_update_with_junk_outcome_rejected(self):
        mid = self._mid()
        did = db.add_delegation(task="Initial",
                                outcome_expected="Initial outcome",
                                manager_id=mid)
        try:
            db.update_delegation(did, manager_id=mid, outcome_expected="n/a")
        except ValueError:
            pass
        else:
            raise AssertionError(
                "update_delegation with junk outcome must raise ValueError")

    def test_update_outcome_equals_existing_task_rejected(self):
        """When outcome_expected is updated without changing task, the
        validator looks up the existing task to compare — the update path
        can't bypass the equal-to-task rule by omitting task from kwargs."""
        mid = self._mid()
        did = db.add_delegation(task="Lead the migration",
                                outcome_expected="Migration shipped",
                                manager_id=mid)
        try:
            db.update_delegation(did, manager_id=mid,
                                 outcome_expected="Lead the migration")
        except ValueError:
            pass
        else:
            raise AssertionError(
                "update outcome == existing task must raise ValueError")


class TestDelegationManagerScoping:
    """The list/count/overdue helpers used to take optional manager_id=None,
    a multi-tenant hole audit caught. Plan PR α tightens them to
    keyword-only required with `if x is None: raise ValueError(...)` (NOT
    `assert`, which is stripped under `python -O`)."""

    def test_list_delegations_rejects_none_manager(self):
        try:
            db.list_delegations(manager_id=None)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "list_delegations(manager_id=None) must raise ValueError")

    def test_get_active_delegations_count_rejects_none_manager(self):
        try:
            db.get_active_delegations_count(manager_id=None)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "get_active_delegations_count(manager_id=None) must raise")

    def test_get_overdue_delegations_rejects_none_manager(self):
        try:
            db.get_overdue_delegations(manager_id=None)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "get_overdue_delegations(manager_id=None) must raise")


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
        db.delete_running_note(nid, manager_id=mid)

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
        db.update_decision(did, manager_id=mid, status="validated",
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
        db.delete_decision(did, manager_id=mid)

        assert len(db.list_decisions(manager_id=mid)) == 0


class TestCoachSuggestions:
    def test_save_and_retrieve(self):
        mid = db.create_manager("mgr", "Mgr", "pass1234")
        db.save_coach_suggestion(mid, "Write your journal", tier="rule",
                                 action_page="Journal")
        suggestion = db.get_todays_suggestion(mid)
        assert suggestion is not None
        assert suggestion["suggestion"] == "Write your journal"
        assert suggestion["tier"] == "rule"
        assert suggestion["action_page"] == "Journal"

    def test_dismiss(self):
        mid = db.create_manager("mgr2", "Mgr2", "pass1234")
        db.save_coach_suggestion(mid, "Do something", tier="rule")
        db.dismiss_todays_suggestion(mid)
        assert db.get_todays_suggestion(mid) is None

    def test_ai_overrides_rule(self):
        mid = db.create_manager("mgr3", "Mgr3", "pass1234")
        db.save_coach_suggestion(mid, "Rule suggestion", tier="rule")
        db.save_coach_suggestion(mid, "AI suggestion", tier="ai")
        suggestion = db.get_todays_suggestion(mid)
        assert suggestion["tier"] == "ai"
        assert suggestion["suggestion"] == "AI suggestion"

    def test_isolation(self):
        m1 = db.create_manager("cs_m1", "M1", "pass1234")
        m2 = db.create_manager("cs_m2", "M2", "pass1234")
        db.save_coach_suggestion(m1, "For M1", tier="rule")
        db.save_coach_suggestion(m2, "For M2", tier="rule")
        assert db.get_todays_suggestion(m1)["suggestion"] == "For M1"
        assert db.get_todays_suggestion(m2)["suggestion"] == "For M2"

    def test_replaces_same_tier_same_day(self):
        mid = db.create_manager("mgr4", "Mgr4", "pass1234")
        db.save_coach_suggestion(mid, "First", tier="rule")
        db.save_coach_suggestion(mid, "Second", tier="rule")
        suggestion = db.get_todays_suggestion(mid)
        assert suggestion["suggestion"] == "Second"


class TestSchemaMigrationSafety:
    """Regression for AUDIT C5 — init_db must NOT delete a legacy DB."""

    def test_legacy_schema_does_not_delete_db(self, tmp_path, monkeypatch):
        import os
        import sqlite3

        legacy_path = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(legacy_path)
        conn.execute(
            "CREATE TABLE journal_entries ("
            "id INTEGER PRIMARY KEY, "
            "manager_id INTEGER NOT NULL, "
            "entry_date TEXT)"
        )
        conn.execute(
            "INSERT INTO journal_entries (manager_id, entry_date) VALUES (1, '2026-01-01')"
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(db, "DB_PATH", legacy_path)
        monkeypatch.setattr(db, "_USE_PG", False)

        try:
            db.init_db()
        except RuntimeError:
            pass  # Acceptable: refuse-to-migrate is the new contract.

        assert os.path.exists(legacy_path), "init_db must never delete an existing database file"
        conn = sqlite3.connect(legacy_path)
        rows = conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()
        conn.close()
        assert rows[0] == 1, "Existing rows must be preserved"


class TestOrphanTableManagerId:
    """Regression for AUDIT C2 / P1.1 — orphan child tables now carry a
    manager_id column populated either explicitly or by parent fallback."""

    ORPHAN_TABLES = (
        "feedback", "goals", "career_conversations",
        "skills", "development_plans", "milestones",
    )

    def test_all_orphan_tables_have_manager_id(self):
        conn = db.get_connection()
        for table in self.ORPHAN_TABLES:
            cols = [c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            assert "manager_id" in cols, f"{table} is missing manager_id column"
        conn.close()

    def _seed_member(self, username="m_orphan"):
        mid = db.create_manager(username, "Orphan Mgr", "pass1234")
        tid = db.add_team_member("Tessa", manager_id=mid)
        return mid, tid

    def test_feedback_inherits_manager_id_from_member(self):
        mid, tid = self._seed_member()
        fid = db.add_feedback(tid, "positive", situation="Great work")
        conn = db.get_connection()
        row = conn.execute("SELECT manager_id FROM feedback WHERE id = ?", (fid,)).fetchone()
        conn.close()
        assert row["manager_id"] == mid

    def test_goal_inherits_manager_id_from_member(self):
        mid, tid = self._seed_member("m_goal")
        gid = db.add_goal(tid, "Q1", "Ship feature X")
        conn = db.get_connection()
        row = conn.execute("SELECT manager_id FROM goals WHERE id = ?", (gid,)).fetchone()
        conn.close()
        assert row["manager_id"] == mid

    def test_skill_inherits_manager_id_from_member(self):
        mid, tid = self._seed_member("m_skill")
        sid = db.add_skill(tid, "Python")
        conn = db.get_connection()
        row = conn.execute("SELECT manager_id FROM skills WHERE id = ?", (sid,)).fetchone()
        conn.close()
        assert row["manager_id"] == mid

    def test_career_conversation_inherits_manager_id_from_member(self):
        mid, tid = self._seed_member("m_career")
        cid = db.add_career_conversation(tid, "2026-01-15", topic="Promotion path")
        conn = db.get_connection()
        row = conn.execute(
            "SELECT manager_id FROM career_conversations WHERE id = ?", (cid,)
        ).fetchone()
        conn.close()
        assert row["manager_id"] == mid

    def test_development_plan_inherits_manager_id_from_member(self):
        mid, tid = self._seed_member("m_devplan")
        pid = db.add_development_plan(tid, "Senior IC track")
        conn = db.get_connection()
        row = conn.execute(
            "SELECT manager_id FROM development_plans WHERE id = ?", (pid,)
        ).fetchone()
        conn.close()
        assert row["manager_id"] == mid

    def test_milestone_inherits_manager_id_from_plan(self):
        mid, tid = self._seed_member("m_milestone")
        pid = db.add_development_plan(tid, "Plan")
        ms_id = db.add_milestone(pid, "First step")
        conn = db.get_connection()
        row = conn.execute(
            "SELECT manager_id FROM milestones WHERE id = ?", (ms_id,)
        ).fetchone()
        conn.close()
        assert row["manager_id"] == mid

    def test_explicit_manager_id_is_honored(self):
        """Explicit manager_id should take precedence over the parent fallback."""
        mid_owner, tid = self._seed_member("m_explicit_owner")
        mid_other = db.create_manager("m_explicit_other", "Other", "pass1234")
        # Pass a DIFFERENT manager_id explicitly — the helper should trust the caller.
        fid = db.add_feedback(tid, "positive", manager_id=mid_other)
        conn = db.get_connection()
        row = conn.execute("SELECT manager_id FROM feedback WHERE id = ?", (fid,)).fetchone()
        conn.close()
        assert row["manager_id"] == mid_other

    def test_backfill_populates_existing_null_rows(self):
        """Upgrade path simulation: a pre-migration DB has rows with NULL
        manager_id, no schema_migrations entry → next init_db() applies the
        backfill migration."""
        mid, tid = self._seed_member("m_backfill")
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO feedback (manager_id, team_member_id, feedback_type, situation) "
            "VALUES (NULL, ?, 'positive', 'old row')",
            (tid,))
        # Force re-application of the orphan-table backfill migration by
        # clearing its ledger row, simulating an unmigrated upgrade.
        conn.execute(
            "DELETE FROM schema_migrations WHERE id = '0002_orphan_table_manager_id'")
        conn.commit()
        conn.close()

        # init_db is guarded once-per-process; force a re-run to simulate
        # the upgrade path.
        db.init_db(force=True)  # Should apply 0002 again, which backfills the NULL row.

        conn = db.get_connection()
        rows = conn.execute(
            "SELECT manager_id FROM feedback WHERE situation = 'old row'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["manager_id"] == mid, "Backfill must populate NULL manager_id from team_member"


class TestCrossManagerScoping:
    """Regression for AUDIT C1 / P1.2 — every reader and mutator must reject
    access to a row owned by a different manager. Test T#3 in PLAN.md."""

    def _two_managers(self):
        m1 = db.create_manager("scope_m1", "M1", "pass1234")
        m2 = db.create_manager("scope_m2", "M2", "pass1234")
        t1 = db.add_team_member("M1 Member", manager_id=m1)
        return m1, m2, t1

    # -- events ----------------------------------------------------------

    def test_get_event_cross_manager(self):
        m1, m2, t1 = self._two_managers()
        eid = db.create_event("M1 meet", "one_on_one", "2026-05-10", "10:00",
                              team_member_id=t1, manager_id=m1)
        assert db.get_event(eid, manager_id=m1) is not None
        assert db.get_event(eid, manager_id=m2) is None

    def test_update_event_cross_manager_rejected(self):
        m1, m2, t1 = self._two_managers()
        eid = db.create_event("M1 meet", "one_on_one", "2026-05-10", "10:00",
                              team_member_id=t1, manager_id=m1)
        db.update_event(eid, manager_id=m2, title="HACKED")
        # Original title preserved
        assert db.get_event(eid, manager_id=m1)["title"] == "M1 meet"

    def test_complete_cancel_event_cross_manager_rejected(self):
        m1, m2, t1 = self._two_managers()
        eid = db.create_event("M1 meet", "one_on_one", "2026-05-10", "10:00",
                              team_member_id=t1, manager_id=m1)
        db.complete_event(eid, manager_id=m2)
        assert db.get_event(eid, manager_id=m1)["status"] == "scheduled"
        db.cancel_event(eid, manager_id=m2)
        assert db.get_event(eid, manager_id=m1)["status"] == "scheduled"

    # -- action_items ----------------------------------------------------

    def test_action_item_cross_manager_rejected(self):
        m1, m2, t1 = self._two_managers()
        eid = db.create_event("M1 meet", "one_on_one", "2026-05-10", "10:00",
                              team_member_id=t1, manager_id=m1)
        aid = db.add_action_item("Follow up", event_id=eid, manager_id=m1)

        db.delete_action_item(aid, manager_id=m2)
        # Still present for owner
        rows = db.list_action_items(manager_id=m1)
        assert any(r["id"] == aid for r in rows)

        db.update_action_item(aid, manager_id=m2, description="HACKED")
        rows = db.list_action_items(manager_id=m1)
        assert next(r["description"] for r in rows if r["id"] == aid) == "Follow up"

        db.complete_action_item(aid, manager_id=m2)
        rows = db.list_action_items(manager_id=m1)
        assert next(r["status"] for r in rows if r["id"] == aid) == "pending"

    # -- feedback --------------------------------------------------------

    def test_feedback_cross_manager_rejected(self):
        m1, m2, t1 = self._two_managers()
        fid = db.add_feedback(t1, "positive", situation="Original")

        db.delete_feedback(fid, manager_id=m2)
        assert any(r["id"] == fid for r in db.list_feedback(manager_id=m1))

        db.update_feedback(fid, manager_id=m2, situation="HACKED")
        assert db.list_feedback(manager_id=m1)[0]["situation"] == "Original"

        # And m2's list_feedback returns nothing
        assert db.list_feedback(manager_id=m2) == []

    # -- goals -----------------------------------------------------------

    def test_goal_cross_manager_rejected(self):
        m1, m2, t1 = self._two_managers()
        gid = db.add_goal(t1, "Q2", "Original")

        db.delete_goal(gid, manager_id=m2)
        assert any(r["id"] == gid for r in db.list_goals(manager_id=m1))

        db.update_goal(gid, manager_id=m2, description="HACKED")
        assert db.list_goals(manager_id=m1)[0]["description"] == "Original"

        assert db.list_goals(manager_id=m2) == []

    # -- journal_entries -------------------------------------------------

    def test_journal_entry_cross_manager_rejected(self):
        m1, m2, _ = self._two_managers()
        eid = db.add_journal_entry("2026-05-01", "daily", "Original", manager_id=m1)
        db.update_journal_entry(eid, manager_id=m2, content="HACKED")
        # Read back via the manager_id-scoped API
        entry = db.get_journal_entry_by_date("2026-05-01", "daily", manager_id=m1)
        assert entry["content"] == "Original"

    # -- skills ----------------------------------------------------------

    def test_skill_cross_manager_rejected(self):
        m1, m2, t1 = self._two_managers()
        sid = db.add_skill(t1, "Original Skill")
        db.delete_skill(sid, manager_id=m2)
        assert any(r["id"] == sid for r in db.list_skills(t1, manager_id=m1))
        db.update_skill(sid, manager_id=m2, skill_name="HACKED")
        assert db.list_skills(t1, manager_id=m1)[0]["skill_name"] == "Original Skill"
        assert db.list_skills(t1, manager_id=m2) == []

    # -- development_plans + milestones ---------------------------------

    def test_development_plan_and_milestone_cross_manager_rejected(self):
        m1, m2, t1 = self._two_managers()
        pid = db.add_development_plan(t1, "Original Plan")
        msid = db.add_milestone(pid, "Original Milestone")

        db.update_development_plan(pid, manager_id=m2, title="HACKED")
        assert db.list_development_plans(t1, manager_id=m1)[0]["title"] == "Original Plan"

        db.complete_milestone(msid, manager_id=m2)
        assert db.list_milestones(pid, manager_id=m1)[0]["completed"] == 0

        # cross-tenant list returns empty
        assert db.list_development_plans(t1, manager_id=m2) == []
        assert db.list_milestones(pid, manager_id=m2) == []

    # -- delegations -----------------------------------------------------

    def test_delegation_cross_manager_rejected(self):
        m1, m2, t1 = self._two_managers()
        did = db.add_delegation(task="Original task", team_member_id=t1,
                                outcome_expected="Original outcome",
                                manager_id=m1)

        db.delete_delegation(did, manager_id=m2)
        active = db.list_delegations(manager_id=m1)
        assert any(r["id"] == did for r in active)

        db.update_delegation(did, manager_id=m2, task="HACKED")
        assert next(r["task"] for r in db.list_delegations(manager_id=m1) if r["id"] == did) == "Original task"

    # -- running_notes ---------------------------------------------------

    def test_running_note_cross_manager_rejected(self):
        m1, m2, t1 = self._two_managers()
        nid = db.add_running_note(t1, "Original note", manager_id=m1)
        db.delete_running_note(nid, manager_id=m2)
        notes = db.list_running_notes(t1, manager_id=m1)
        assert any(r["id"] == nid for r in notes)

    # -- decisions -------------------------------------------------------

    def test_decision_cross_manager_rejected(self):
        m1, m2, _ = self._two_managers()
        did = db.add_decision(title="Original Decision", manager_id=m1)

        db.delete_decision(did, manager_id=m2)
        assert any(r["id"] == did for r in db.list_decisions(manager_id=m1))

        db.update_decision(did, manager_id=m2, title="HACKED")
        assert next(r["title"] for r in db.list_decisions(manager_id=m1) if r["id"] == did) == "Original Decision"

    # -- career_conversations -------------------------------------------

    def test_career_conversation_cross_manager_rejected(self):
        m1, m2, t1 = self._two_managers()
        db.add_career_conversation(t1, "2026-05-10", topic="Promo path")
        assert len(db.list_career_conversations(t1, manager_id=m1)) == 1
        assert db.list_career_conversations(t1, manager_id=m2) == []

    # -- aggregations: get_member_summary / timeline / prep --------------

    def test_member_summary_cross_manager_rejected(self):
        m1, m2, t1 = self._two_managers()
        assert db.get_member_summary(t1, manager_id=m1) is not None
        assert db.get_member_summary(t1, manager_id=m2) is None

    def test_member_timeline_cross_manager_empty(self):
        m1, m2, t1 = self._two_managers()
        db.create_event("M1 meet", "one_on_one", "2026-05-10", "10:00",
                        team_member_id=t1, manager_id=m1)
        db.add_feedback(t1, "positive", situation="Note")
        assert len(db.get_member_timeline(t1, manager_id=m1)) >= 2
        assert db.get_member_timeline(t1, manager_id=m2) == []

    def test_pre_meeting_prep_cross_manager_rejected(self):
        m1, m2, t1 = self._two_managers()
        assert db.get_pre_meeting_prep(t1, manager_id=m1) is not None
        assert db.get_pre_meeting_prep(t1, manager_id=m2) is None


class TestRaceConditionFreeUpserts:
    """Regression for AUDIT H6 / P2.6 — save_self_assessment and
    save_coach_suggestion must use atomic UPSERT under a unique index, not
    delete-then-insert (race-prone under autocommit)."""

    def test_unique_index_on_coach_suggestions(self):
        """The (manager_id, suggestion_date, tier) unique index must exist —
        without it, INSERT ... ON CONFLICT degrades silently."""
        with db._connect() as conn:
            cur = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name='ux_coach_suggestions_mid_date_tier'"
            )
            row = cur.fetchone()
        assert row is not None

    def test_unique_index_on_self_assessments(self):
        with db._connect() as conn:
            cur = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name='ux_self_assessments_mid_week_dim'"
            )
            row = cur.fetchone()
        assert row is not None

    def test_save_coach_suggestion_idempotent_no_duplicate(self):
        """Repeated saves for the same (mid, date, tier) leave exactly one row."""
        mid = db.create_manager("race_cs", "C", "pass1234")
        for content in ("first", "second", "third"):
            db.save_coach_suggestion(mid, content, tier="rule",
                                     suggestion_date="2026-05-02")
        with db._connect() as conn:
            cur = conn.execute(
                "SELECT COUNT(*) AS c, MAX(suggestion) AS s "
                "FROM coach_suggestions "
                "WHERE manager_id = ? AND suggestion_date = ? AND tier = ?",
                (mid, "2026-05-02", "rule"))
            row = cur.fetchone()
        assert row["c"] == 1, "UPSERT must not create duplicate rows"
        assert row["s"] == "third"

    def test_save_self_assessment_idempotent_no_duplicate(self):
        mid = db.create_manager("race_sa", "S", "pass1234")
        # Save the same dimension twice with different scores; expect one row.
        db.save_self_assessment("2026-W18", {"clarity": 3, "growth": 4}, manager_id=mid)
        db.save_self_assessment("2026-W18", {"clarity": 5, "growth": 4, "feedback": 3},
                                manager_id=mid)
        with db._connect() as conn:
            cur = conn.execute(
                "SELECT dimension, score FROM self_assessments "
                "WHERE manager_id = ? AND week_date = ? "
                "ORDER BY dimension",
                (mid, "2026-W18"))
            rows = cur.fetchall()
        scores = {r["dimension"]: r["score"] for r in rows}
        assert scores == {"clarity": 5, "feedback": 3, "growth": 4}, scores

    def test_save_coach_suggestion_resets_dismissed_on_update(self):
        """When the suggestion is overwritten via UPSERT, the dismissed flag
        resets so the user actually sees the new content."""
        mid = db.create_manager("race_cs2", "C", "pass1234")
        db.save_coach_suggestion(mid, "old", tier="rule", suggestion_date="2026-05-02")
        # Manually mark dismissed
        with db._connect() as conn:
            conn.execute(
                "UPDATE coach_suggestions SET dismissed = 1 "
                "WHERE manager_id = ? AND suggestion_date = ? AND tier = ?",
                (mid, "2026-05-02", "rule"))
            conn.commit()
        # Save again — UPSERT must reset dismissed to 0
        db.save_coach_suggestion(mid, "new", tier="rule", suggestion_date="2026-05-02")
        with db._connect() as conn:
            row = conn.execute(
                "SELECT suggestion, dismissed FROM coach_suggestions "
                "WHERE manager_id = ? AND suggestion_date = ? AND tier = ?",
                (mid, "2026-05-02", "rule")).fetchone()
        assert row["suggestion"] == "new"
        assert row["dismissed"] == 0

    def test_concurrent_save_coach_suggestion_via_threads(self):
        """Two threads racing the same (mid, date, tier) write must end with
        exactly one row, no exceptions. Simulates the H6 race."""
        import threading
        mid = db.create_manager("race_cs3", "C", "pass1234")
        errors = []

        def worker(text):
            try:
                db.save_coach_suggestion(mid, text, tier="rule",
                                         suggestion_date="2026-05-02")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(f"t{i}",))
                   for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent saves must not raise: {errors}"
        with db._connect() as conn:
            cur = conn.execute(
                "SELECT COUNT(*) AS c FROM coach_suggestions "
                "WHERE manager_id = ? AND suggestion_date = ? AND tier = ?",
                (mid, "2026-05-02", "rule"))
            count = cur.fetchone()["c"]
        assert count == 1, f"Expected exactly 1 row, got {count}"


class TestQueryEfficiency:
    """Regressions for AUDIT M4 / P4.2 — fewer queries, batch fetches,
    once-per-process init_db()."""

    def test_get_pending_action_items_uses_in_clause(self):
        """Was two queries (status='pending', then status='in_progress');
        now one query with IN. We verify by counting connections during
        the call."""
        mid = db.create_manager("perf_qe", "P", "pass1234")
        eid = db.create_event("e", "one_on_one", "2026-05-01", "10:00", manager_id=mid)
        db.add_action_item("a", event_id=eid, manager_id=mid)
        db.add_action_item("b", event_id=eid, manager_id=mid)
        # Patch get_connection to count calls
        original = db.get_connection
        calls = {"n": 0}

        def counting():
            calls["n"] += 1
            return original()

        try:
            db.get_connection = counting
            rows = db.get_pending_action_items(manager_id=mid)
        finally:
            db.get_connection = original
        assert len(rows) == 2
        assert calls["n"] == 1, f"Expected 1 connection, got {calls['n']}"

    def test_list_action_items_status_iterable(self):
        """Status can be a tuple/list — produces an IN (?, ?) clause."""
        mid = db.create_manager("perf_status", "P", "pass1234")
        eid = db.create_event("e", "one_on_one", "2026-05-01", "10:00", manager_id=mid)
        a1 = db.add_action_item("pending one", event_id=eid, manager_id=mid)
        a2 = db.add_action_item("in-progress one", event_id=eid, manager_id=mid)
        db.update_action_item_status(a2, "in_progress", manager_id=mid)

        rows = db.list_action_items(
            status=("pending", "in_progress"), manager_id=mid)
        ids = {r["id"] for r in rows}
        assert ids == {a1, a2}

    def test_list_milestones_for_plans_one_query(self):
        """Was N+1 (one query per plan); now one query for all plans.
        Connection-count test confirms it."""
        mid = db.create_manager("perf_ms", "P", "pass1234")
        tid = db.add_team_member("Alice", manager_id=mid)
        plan_ids = [
            db.add_development_plan(tid, f"Plan {i}", manager_id=mid)
            for i in range(3)
        ]
        for pid in plan_ids:
            db.add_milestone(pid, f"Milestone for {pid}", manager_id=mid)
            db.add_milestone(pid, f"Second for {pid}", manager_id=mid)

        original = db.get_connection
        calls = {"n": 0}

        def counting():
            calls["n"] += 1
            return original()

        try:
            db.get_connection = counting
            result = db.list_milestones_for_plans(plan_ids, manager_id=mid)
        finally:
            db.get_connection = original

        assert calls["n"] == 1, f"Expected 1 connection, got {calls['n']}"
        assert set(result.keys()) == set(plan_ids)
        for pid in plan_ids:
            assert len(result[pid]) == 2

    def test_list_milestones_for_plans_empty_input(self):
        mid = db.create_manager("perf_empty", "P", "pass1234")
        assert db.list_milestones_for_plans([], manager_id=mid) == {}

    def test_list_milestones_for_plans_cross_manager_safe(self):
        """Cross-manager call returns empty for each requested plan id."""
        m1 = db.create_manager("perf_xm1", "M1", "pass1234")
        m2 = db.create_manager("perf_xm2", "M2", "pass1234")
        t1 = db.add_team_member("Alice", manager_id=m1)
        pid = db.add_development_plan(t1, "Plan", manager_id=m1)
        db.add_milestone(pid, "M1 milestone", manager_id=m1)
        result = db.list_milestones_for_plans([pid], manager_id=m2)
        assert result == {pid: []}

    def test_init_db_is_once_per_process(self, monkeypatch):
        """The default init_db() returns immediately after the first
        success per process. force=True overrides for tests."""
        monkeypatch.setattr(db, "_INIT_DB_DONE", True)
        called = {"n": 0}
        original = db._run_migrations

        def counting(conn):
            called["n"] += 1
            return original(conn)

        monkeypatch.setattr(db, "_run_migrations", counting)
        db.init_db()  # Should be a no-op (flag already True)
        assert called["n"] == 0, "init_db without force must skip when already done"

        db.init_db(force=True)
        assert called["n"] == 1, "force=True must run the migrations"


class TestHotPathIndexes:
    """Regression for AUDIT M5 / P4.1 — every hot WHERE column listed in
    the audit must have a backing index after init_db()."""

    EXPECTED_INDEXES = (
        ("events", "ix_events_manager_date_status"),
        ("action_items", "ix_action_items_manager_status_due"),
        ("journal_entries", "ix_journal_entries_manager_date"),
        ("feedback", "ix_feedback_member_created"),
        ("team_members", "ix_team_members_manager"),
        ("running_notes", "ix_running_notes_member_date"),
        ("delegations", "ix_delegations_manager_status_checkin"),
        ("coach_suggestions", "ix_coach_suggestions_manager_date"),
    )

    def test_all_hot_path_indexes_exist(self):
        with db._connect() as conn:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'")
            names = {r["name"] if isinstance(r, dict) else r[0]
                     for r in cur.fetchall()}
        missing = [(t, ix) for t, ix in self.EXPECTED_INDEXES if ix not in names]
        assert not missing, f"Missing hot-path indexes: {missing}"

    def test_indexes_use_correct_columns(self):
        """Sanity-check that each index covers the expected columns in the
        expected order (so the planner can use it for the audit's queries)."""
        expected = {
            "ix_events_manager_date_status":
                ("manager_id", "scheduled_date", "status"),
            "ix_action_items_manager_status_due":
                ("manager_id", "status", "due_date"),
            "ix_journal_entries_manager_date":
                ("manager_id", "entry_date"),
            "ix_feedback_member_created":
                ("team_member_id", "created_at"),
            "ix_team_members_manager":
                ("manager_id",),
            "ix_running_notes_member_date":
                ("team_member_id", "note_date"),
            "ix_delegations_manager_status_checkin":
                ("manager_id", "status", "check_in_date"),
            "ix_coach_suggestions_manager_date":
                ("manager_id", "suggestion_date"),
        }
        with db._connect() as conn:
            for ix_name, expected_cols in expected.items():
                cur = conn.execute(f"PRAGMA index_info({ix_name})")
                actual = tuple(r[2] for r in cur.fetchall())
                assert actual == expected_cols, \
                    f"{ix_name}: expected {expected_cols}, got {actual}"


class TestPgOutageBehaviour:
    """A configured-but-unreachable Postgres always raises
    DatabaseUnavailableError — the SQLite fallback is disabled whenever
    DATABASE_URL is set, regardless of MANAGER_TOOL_ENV. Silent fallback
    would orphan writes when the real database returned."""

    def _force_pg_failure(self, monkeypatch):
        """Pretend Postgres is configured and raises on connect."""
        monkeypatch.setattr(db, "_USE_PG", True)
        monkeypatch.setattr(db, "_get_pg_url", lambda: "postgres://x/y")

        class _BoomPsycopg2:
            class extras:
                class RealDictCursor:
                    pass

            @staticmethod
            def connect(*_a, **_kw):
                raise RuntimeError("synthetic Postgres outage")

        import sys
        monkeypatch.setitem(sys.modules, "psycopg2", _BoomPsycopg2)
        monkeypatch.setitem(sys.modules, "psycopg2.extras", _BoomPsycopg2.extras)

    def test_is_production_reads_env_var(self, monkeypatch):
        monkeypatch.setenv("MANAGER_TOOL_ENV", "prod")
        assert db._is_production() is True
        monkeypatch.setenv("MANAGER_TOOL_ENV", "dev")
        assert db._is_production() is False
        monkeypatch.delenv("MANAGER_TOOL_ENV", raising=False)
        assert db._is_production() is False

    def test_prod_fails_loud_on_pg_outage(self, monkeypatch):
        monkeypatch.setenv("MANAGER_TOOL_ENV", "prod")
        self._force_pg_failure(monkeypatch)
        try:
            db.get_connection()
        except db.DatabaseUnavailableError:
            return
        raise AssertionError(
            "get_connection must raise DatabaseUnavailableError when "
            "Postgres is unreachable; SQLite fallback is disallowed"
        )

    def test_non_prod_also_fails_loud_on_pg_outage(self, monkeypatch):
        """The fallback used to kick in when MANAGER_TOOL_ENV != prod, but
        that produced split-brain data (writes during the outage landed in
        an ephemeral SQLite file and were orphaned when PG returned). Now
        the rule is simpler: DATABASE_URL set + PG unreachable always raises."""
        monkeypatch.delenv("MANAGER_TOOL_ENV", raising=False)
        self._force_pg_failure(monkeypatch)
        try:
            db.get_connection()
        except db.DatabaseUnavailableError:
            return
        raise AssertionError(
            "get_connection must raise even outside prod — silent SQLite "
            "fallback splits writes across two backends"
        )

    def test_pg_failed_flags_set_after_outage(self, monkeypatch):
        """Status flags are set before the exception unwinds, so any
        diagnostic surface that reads pg_connection_failed() still gets
        the redacted error message."""
        monkeypatch.delenv("MANAGER_TOOL_ENV", raising=False)
        self._force_pg_failure(monkeypatch)
        try:
            db.get_connection()
        except Exception:
            pass
        failed, msg = db.pg_connection_failed()
        assert failed is True
        assert "synthetic Postgres outage" in msg


class TestServerSideSessions:
    """Regression for AUDIT H3 / P2.3 — sessions are server-side, expire,
    and can be revoked."""

    def test_create_validate_revoke_roundtrip(self):
        mid = db.create_manager("sess_user", "Sess", "pass1234")
        token = db.create_session(mid)
        assert isinstance(token, str) and len(token) > 20

        assert db.validate_session(token) == mid
        # Validate refreshes last_seen (no exception, still returns mid).
        assert db.validate_session(token) == mid

        db.revoke_session(token)
        assert db.validate_session(token) is None

    def test_unknown_token_returns_none(self):
        assert db.validate_session("definitely-not-a-real-token") is None
        assert db.validate_session("") is None

    def test_expired_session_rejected_and_cleaned_up(self):
        mid = db.create_manager("exp_user", "Exp", "pass1234")
        token = db.create_session(mid, ttl_seconds=-1)  # already expired
        assert db.validate_session(token) is None
        # After validate_session, the expired row should be deleted.
        with db._connect() as conn:
            row = conn.execute(
                "SELECT id FROM sessions WHERE id = ?", (token,)).fetchone()
        assert row is None, "validate_session must drop expired rows"

    def test_user_agent_binding(self):
        mid = db.create_manager("ua_user", "UA", "pass1234")
        ua_a = db.hash_user_agent("Mozilla/5.0 (Browser A)")
        ua_b = db.hash_user_agent("Mozilla/5.0 (Browser B)")
        token = db.create_session(mid, user_agent_hash=ua_a)
        # Same UA succeeds; different UA rejected.
        assert db.validate_session(token, ua_a) == mid
        assert db.validate_session(token, ua_b) is None

    def test_revoke_idempotent(self):
        mid = db.create_manager("rev_user", "Rev", "pass1234")
        token = db.create_session(mid)
        db.revoke_session(token)
        db.revoke_session(token)  # second call must not raise
        db.revoke_session("never-existed")

    def test_cleanup_expired(self):
        mid = db.create_manager("cleanup_user", "C", "pass1234")
        live = db.create_session(mid, ttl_seconds=3600)
        dead = db.create_session(mid, ttl_seconds=-1)
        db.cleanup_expired_sessions()
        assert db.validate_session(live) == mid
        with db._connect() as conn:
            row = conn.execute(
                "SELECT id FROM sessions WHERE id = ?", (dead,)).fetchone()
        assert row is None


class TestPersistentRateLimit:
    """Regression for AUDIT H2 / P2.3 — failed logins persist across tabs and
    enforce exponential-backoff lockout."""

    def test_first_few_failures_no_lockout(self):
        for _ in range(db.LOGIN_FAIL_THRESHOLD - 1):
            db.record_failed_login("ratelimit1")
        assert db.get_lockout_until("ratelimit1") is None

    def test_threshold_triggers_lockout(self):
        for _ in range(db.LOGIN_FAIL_THRESHOLD):
            db.record_failed_login("ratelimit2")
        locked_until = db.get_lockout_until("ratelimit2")
        assert locked_until is not None
        assert locked_until > datetime.now()

    def test_lockout_grows_exponentially(self):
        for _ in range(db.LOGIN_FAIL_THRESHOLD):
            db.record_failed_login("ratelimit3")
        first = db.get_lockout_until("ratelimit3")
        assert first is not None
        first_window = (first - datetime.now()).total_seconds()

        # Two more failures → 4x the base lockout (2^2).
        db.record_failed_login("ratelimit3")
        db.record_failed_login("ratelimit3")
        second = db.get_lockout_until("ratelimit3")
        assert second is not None
        second_window = (second - datetime.now()).total_seconds()
        assert second_window > first_window * 2

    def test_clear_on_success(self):
        for _ in range(db.LOGIN_FAIL_THRESHOLD):
            db.record_failed_login("ratelimit4")
        assert db.get_lockout_until("ratelimit4") is not None
        db.clear_failed_logins("ratelimit4")
        assert db.get_lockout_until("ratelimit4") is None

    def test_username_normalised_for_lockout(self):
        """Lockout key is case-insensitive — can't bypass by changing case."""
        for _ in range(db.LOGIN_FAIL_THRESHOLD):
            db.record_failed_login("MixedCase")
        assert db.get_lockout_until("mixedcase") is not None
        assert db.get_lockout_until("MIXEDCASE") is not None

    def test_lockout_persists_across_invocations(self):
        """Mimics the H2 attack — multiple processes/tabs see the same counter."""
        for _ in range(db.LOGIN_FAIL_THRESHOLD):
            db.record_failed_login("ratelimit5")
        # Simulate "new tab": just call again.
        assert db.get_lockout_until("ratelimit5") is not None


class TestConnectionLifecycle:
    """Regression for AUDIT H1 / P2.2 — connections must close on exception
    paths, not just happy paths."""

    def test_connect_closes_on_exception(self):
        """The _connect() context manager must close the connection even when
        the body raises."""
        closed = []
        real_get_conn = db.get_connection

        class _Tracker:
            def __init__(self, conn):
                self._conn = conn

            def close(self):
                closed.append(True)
                return self._conn.close()

            def __getattr__(self, name):
                return getattr(self._conn, name)

        try:
            db.get_connection = lambda: _Tracker(real_get_conn())
            try:
                with db._connect() as conn:
                    raise RuntimeError("synthetic")
            except RuntimeError:
                pass
        finally:
            db.get_connection = real_get_conn

        assert closed == [True], "_connect must close the connection on exception"

    def test_create_manager_closes_on_failure(self):
        """create_manager wraps INSERT in _connect; even on duplicate username
        (IntegrityError) the connection must be released."""
        db.create_manager("dup_user", "First", "pass1234")
        # Second create with same username should return None and not leak.
        result = db.create_manager("dup_user", "Second", "pass1234")
        assert result is None
        # If we leaked, subsequent operations could hang or fail; do another op
        # to confirm the database is still usable.
        assert db.manager_exists("dup_user") is True


class TestMigrationRunner:
    """Regression for AUDIT C5 / P2.1 — schema_migrations ledger + sequenced
    migrations applied automatically at startup."""

    def test_schema_migrations_table_exists(self):
        conn = db.get_connection()
        cols = db._table_columns(conn, "schema_migrations")
        conn.close()
        assert "id" in cols
        assert "applied_at" in cols

    def test_all_migrations_applied_after_init_db(self):
        conn = db.get_connection()
        cur = conn.execute("SELECT id FROM schema_migrations")
        applied = {r["id"] if isinstance(r, dict) else r[0] for r in cur.fetchall()}
        conn.close()
        expected = {mid for mid, _ in db._MIGRATIONS}
        assert expected.issubset(applied), \
            f"Missing migrations: {expected - applied}"

    def test_migrations_idempotent(self):
        """Calling init_db() twice in a row leaves the ledger unchanged."""
        conn = db.get_connection()
        before = conn.execute(
            "SELECT id, applied_at FROM schema_migrations ORDER BY id"
        ).fetchall()
        conn.close()

        db.init_db()  # second call — should be a no-op

        conn = db.get_connection()
        after = conn.execute(
            "SELECT id, applied_at FROM schema_migrations ORDER BY id"
        ).fetchall()
        conn.close()
        # Same IDs and same applied_at timestamps (no re-application).
        assert [(r["id"], r["applied_at"]) for r in before] == \
               [(r["id"], r["applied_at"]) for r in after]

    def test_migration_failure_propagates(self, monkeypatch):
        """If a migration raises, the runner re-raises and does NOT record the
        ledger row — ensuring the failure is loud and the migration retries."""
        boom_id = "9999_boom"

        def boom(_):
            raise RuntimeError("synthetic failure")

        # Inject a failing migration into the registry, then re-run.
        monkeypatch.setattr(db, "_MIGRATIONS", db._MIGRATIONS + [(boom_id, boom)])

        conn = db.get_connection()
        try:
            try:
                db._run_migrations(conn)
            except RuntimeError:
                pass
            else:
                raise AssertionError("Failing migration must propagate")
            cur = conn.execute(
                "SELECT id FROM schema_migrations WHERE id = ?", (boom_id,))
            assert cur.fetchone() is None, \
                "Failed migration must NOT be recorded as applied"
        finally:
            conn.close()


class TestUpcomingAggregator:
    """get_upcoming_aggregate + get_overdue_aggregate cover the four streams
    fed into the Upcoming page (events + action_items + delegations + goals).
    Multi-tenancy on the aggregator is the high-blast-radius failure mode —
    a missing manager_id predicate on any subquery leaks data across tenants.

    Note: SQLite-only (conftest pins _USE_PG=False), so these tests verify
    the predicate exists. Cross-tenant leak under PG-specific predicate
    quirks (e.g. an array-typed predicate swap) is the smoke job's
    responsibility — see scripts/smoke_pg.py."""

    def _today_iso(self):
        from datetime import date
        return date.today().isoformat()

    def _in_n_days(self, n):
        from datetime import date, timedelta
        return (date.today() + timedelta(days=n)).isoformat()

    def test_rejects_none_manager(self):
        """assert manager_id is not None must fire — no implicit cross-tenant
        return-empty fallback."""
        try:
            db.get_upcoming_aggregate(manager_id=None)
        except AssertionError:
            pass
        else:
            raise AssertionError("get_upcoming_aggregate(manager_id=None) must raise")
        try:
            db.get_overdue_aggregate(manager_id=None)
        except AssertionError:
            pass
        else:
            raise AssertionError("get_overdue_aggregate(manager_id=None) must raise")

    def test_returns_seeded_rows_within_window(self):
        """All four streams surface when seeded within the 7-day window."""
        mid = db.create_manager("agg_mgr1", "Agg1", "pass1234")
        member_id = db.add_team_member("Aggie", manager_id=mid)
        soon = self._in_n_days(2)

        db.create_event("1:1 with Aggie", "one_on_one", soon, "10:00",
                        team_member_id=member_id, manager_id=mid)
        db.add_action_item("Review Aggie's draft", due_date=soon,
                           manager_id=mid)
        db.add_delegation("Own Q3 onboarding", team_member_id=member_id,
                          outcome_expected="Onboarding plan rolled out",
                          check_in_date=soon, manager_id=mid)
        db.add_goal(member_id, "Q2 2026", "Ship the migration",
                    target_date=soon, manager_id=mid)

        rows = db.get_upcoming_aggregate(manager_id=mid)
        types = sorted({r["type"] for r in rows})
        assert types == ["check-in", "event", "goal", "todo"], \
            f"all four streams must appear; got {types}"

    def test_filters_terminal_goal_states(self):
        """Goals filter is the positive form — partially_met / not_met are
        terminal historical states and must not appear in Upcoming even
        when target_date falls inside the window."""
        mid = db.create_manager("agg_mgr2", "Agg2", "pass1234")
        member_id = db.add_team_member("Done Person", manager_id=mid)
        soon = self._in_n_days(3)

        gid_active = db.add_goal(member_id, "Q2 2026", "Active goal",
                                 target_date=soon, manager_id=mid)
        gid_done = db.add_goal(member_id, "Q2 2026", "Already-done goal",
                               target_date=soon, manager_id=mid)
        # Update the second to a terminal state
        db.update_goal(gid_done, manager_id=mid, status="partially_met")

        rows = db.get_upcoming_aggregate(manager_id=mid)
        goal_titles = [r["title"] for r in rows if r["type"] == "goal"]
        assert "Active goal" in goal_titles
        assert "Already-done goal" not in goal_titles, \
            "partially_met goal must not appear in Upcoming"

    def test_overdue_includes_past_due_actions(self):
        """get_overdue_aggregate surfaces past-due, non-terminal items."""
        from datetime import date, timedelta
        mid = db.create_manager("agg_mgr3", "Agg3", "pass1234")
        past = (date.today() - timedelta(days=5)).isoformat()
        db.add_action_item("Forgot to do this", due_date=past, manager_id=mid)

        rows = db.get_overdue_aggregate(manager_id=mid)
        titles = [r["title"] for r in rows if r["type"] == "todo"]
        assert "Forgot to do this" in titles, \
            "past-due pending action must appear in overdue aggregate"

    def test_predicate_present_for_cross_tenant(self):
        """Same-DB cross-tenant check on SQLite. The smoke job repeats this
        on real PG against PG-specific predicate quirks; this is the fast
        SQLite-side regression that the predicate exists at all."""
        m1 = db.create_manager("agg_a", "A", "pass1234")
        m2 = db.create_manager("agg_b", "B", "pass1234")
        soon = self._in_n_days(2)
        # Seed under both managers using the same app helpers the form uses.
        member_a = db.add_team_member("A's report", manager_id=m1)
        member_b = db.add_team_member("B's report", manager_id=m2)
        db.add_action_item("A's task", due_date=soon, manager_id=m1)
        db.add_action_item("B's task", due_date=soon, manager_id=m2)
        db.add_delegation("A's deleg", team_member_id=member_a,
                          outcome_expected="A's deleg outcome",
                          check_in_date=soon, manager_id=m1)
        db.add_delegation("B's deleg", team_member_id=member_b,
                          outcome_expected="B's deleg outcome",
                          check_in_date=soon, manager_id=m2)

        a_rows = db.get_upcoming_aggregate(manager_id=m1)
        b_rows = db.get_upcoming_aggregate(manager_id=m2)
        a_titles = {r["title"] for r in a_rows}
        b_titles = {r["title"] for r in b_rows}
        # Bidirectional: each manager sees ONLY their own rows.
        assert "A's task" in a_titles and "A's deleg" in a_titles
        assert "B's task" not in a_titles and "B's deleg" not in a_titles
        assert "B's task" in b_titles and "B's deleg" in b_titles
        assert "A's task" not in b_titles and "A's deleg" not in b_titles

    def test_migration_idempotent(self):
        """0008_goals_target_date is idempotent: a second _run_migrations
        run is a no-op (column already present, INDEX IF NOT EXISTS)."""
        mid = db.create_manager("agg_mig", "Mig", "pass1234")
        # Force-re-run: the conftest fixture already ran migrations once on
        # a fresh db. Re-running must not raise.
        with db._connect() as conn:
            db._run_migrations(conn)
        # Sanity: target_date column exists.
        cols = []
        with db._connect() as conn:
            cur = db._exec(conn, "PRAGMA table_info(goals)")
            cols = [r[1] for r in cur.fetchall()]
        assert "target_date" in cols, \
            "0008 migration must add goals.target_date"


class TestAddMonthsAnchored:
    """_add_months_anchored implements Algo A (anchor preserved). Round-2
    review caught that Algo B (advance-from-previous-clamped) would silently
    drift after the first short month, permanently losing the anchor day."""

    def test_basic_addition(self):
        from datetime import date
        assert db._add_months_anchored(date(2026, 1, 15), 1) == date(2026, 2, 15)
        assert db._add_months_anchored(date(2026, 1, 15), 12) == date(2027, 1, 15)

    def test_zero_months_returns_start(self):
        from datetime import date
        assert db._add_months_anchored(date(2026, 5, 31), 0) == date(2026, 5, 31)

    def test_jan_31_clamps_to_short_months_but_preserves_anchor(self):
        """Jan 31 + 1mo → Feb 28, + 2mo → Mar 31 (back to 31st), + 3mo → Apr 30,
        + 4mo → May 31. Algo A iterates from start, so the cadence keeps
        finding the 31st whenever the target month has one."""
        from datetime import date
        start = date(2026, 1, 31)
        assert db._add_months_anchored(start, 1) == date(2026, 2, 28)
        assert db._add_months_anchored(start, 2) == date(2026, 3, 31)
        assert db._add_months_anchored(start, 3) == date(2026, 4, 30)
        assert db._add_months_anchored(start, 4) == date(2026, 5, 31)

    def test_leap_year_feb_29(self):
        """Jan 31, 2024 (leap year) + 1mo → Feb 29 (not 28)."""
        from datetime import date
        assert db._add_months_anchored(date(2024, 1, 31), 1) == date(2024, 2, 29)

    def test_quarterly_aug_31_anchor_preserved(self):
        """Quarterly = +3 months from start each iter. Aug 31 → Nov 30 → Feb 28 →
        May 31 → Aug 31 → Nov 30. The cadence finds the 31st whenever it can."""
        from datetime import date
        start = date(2026, 8, 31)
        assert db._add_months_anchored(start, 3) == date(2026, 11, 30)
        assert db._add_months_anchored(start, 6) == date(2027, 2, 28)
        assert db._add_months_anchored(start, 9) == date(2027, 5, 31)
        assert db._add_months_anchored(start, 12) == date(2027, 8, 31)


class TestMaterializeInTxn:
    """_materialize_in_txn must land both INSERTs or neither. PR 4 round-3
    review caught that the naïve loop-through-_exec_returning_id pattern
    is broken on both backends — PG's autocommit makes per-statement
    rollback meaningless; SQLite's _exec_returning_id auto-commits the
    parent before children even start."""

    def test_rollback_on_children_failure_no_orphan_parent(self):
        """Force a NOT NULL violation on a child INSERT and assert the
        parent did not commit. On SQLite this proves we don't go through
        the auto-committing _exec_returning_id; on PG it proves the
        explicit BEGIN/COMMIT works around autocommit=True."""
        mid = db.create_manager("mat_mgr1", "Mat1", "pass1234")
        # Title column is NOT NULL — child row with title=None must fail.
        parent_sql = ("INSERT INTO events (title, event_type, scheduled_date, "
                     "scheduled_time, manager_id) VALUES (?, ?, ?, ?, ?)")
        parent_params = ("Parent", "one_on_one", "2026-06-01", "10:00", mid)
        children_sql = ("INSERT INTO events (title, event_type, scheduled_date, "
                       "scheduled_time, manager_id, parent_event_id) "
                       "VALUES (?, ?, ?, ?, ?, ?)")
        bad_children = [(None, "one_on_one", "2026-06-08", "10:00", mid)]

        with db._connect() as conn:
            try:
                db._materialize_in_txn(conn, parent_sql, parent_params,
                                       children_sql, bad_children)
            except Exception:
                pass
            else:
                raise AssertionError("expected children INSERT to fail")
            # Assert no orphan parent landed.
            cur = db._exec(conn, "SELECT COUNT(*) FROM events WHERE manager_id = ?",
                          (mid,))
            count = cur.fetchone()[0]
        assert count == 0, f"orphan parent landed: {count} event(s) under manager"

    def test_cap_refuses_too_many_children(self):
        """Cap is 32. Generating 33 must raise ValueError before any DB write."""
        mid = db.create_manager("mat_mgr2", "Mat2", "pass1234")
        parent_sql = ("INSERT INTO events (title, event_type, scheduled_date, "
                     "scheduled_time, manager_id) VALUES (?, ?, ?, ?, ?)")
        parent_params = ("Parent", "one_on_one", "2026-06-01", "10:00", mid)
        children_sql = ("INSERT INTO events (title, event_type, scheduled_date, "
                       "scheduled_time, manager_id, parent_event_id) "
                       "VALUES (?, ?, ?, ?, ?, ?)")
        too_many = [("c", "one_on_one", "2026-06-08", "10:00", mid)
                    for _ in range(33)]
        with db._connect() as conn:
            try:
                db._materialize_in_txn(conn, parent_sql, parent_params,
                                       children_sql, too_many)
            except ValueError:
                pass
            else:
                raise AssertionError("expected ValueError for >32 children")
            cur = db._exec(conn, "SELECT COUNT(*) FROM events WHERE manager_id = ?",
                          (mid,))
            count = cur.fetchone()[0]
        assert count == 0, f"cap must refuse before any write; got {count} rows"


class TestRecurringEvents:
    """create_recurring_events end-to-end: weekly + monthly + quarterly,
    parent-delete leaves children, expiry-warning surfaces correctly."""

    def test_weekly_creates_full_series(self):
        from datetime import date
        mid = db.create_manager("rec_mgr1", "Rec1", "pass1234")
        member = db.add_team_member("Weekly Person", manager_id=mid)
        pid = db.create_recurring_events(
            title="Weekly 1:1", event_type="one_on_one",
            start_date=date(2026, 6, 1), scheduled_time="10:00",
            rule="weekly", team_member_id=member, manager_id=mid)
        # 12 events total: 1 parent + 11 children
        with db._connect() as conn:
            cur = db._exec(conn,
                "SELECT COUNT(*) FROM events WHERE manager_id = ?", (mid,))
            count = cur.fetchone()[0]
            cur = db._exec(conn,
                "SELECT COUNT(*) FROM events WHERE parent_event_id = ?", (pid,))
            child_count = cur.fetchone()[0]
        assert count == 12, f"weekly series should have 12 events, got {count}"
        assert child_count == 11

    def test_monthly_on_31st_clamps_correctly(self):
        from datetime import date
        mid = db.create_manager("rec_mgr2", "Rec2", "pass1234")
        pid = db.create_recurring_events(
            title="Month-end review", event_type="quarterly_review",
            start_date=date(2026, 1, 31), scheduled_time="14:00",
            rule="monthly", manager_id=mid)
        with db._connect() as conn:
            cur = db._exec(conn,
                "SELECT scheduled_date FROM events WHERE manager_id = ? "
                "ORDER BY scheduled_date", (mid,))
            dates = [r[0] for r in cur.fetchall()]
        # Jan 31 → Feb 28 → Mar 31 → Apr 30 → May 31 → Jun 30 → Jul 31 → ...
        assert "2026-01-31" in dates
        assert "2026-02-28" in dates
        assert "2026-03-31" in dates
        assert "2026-04-30" in dates
        assert "2026-05-31" in dates

    def test_until_date_truncates_series(self):
        from datetime import date
        mid = db.create_manager("rec_mgr3", "Rec3", "pass1234")
        # Weekly with until_date 3 weeks out → should produce 4 events
        # (start + 3 weekly children).
        db.create_recurring_events(
            title="Truncated", event_type="check_in",
            start_date=date(2026, 6, 1), scheduled_time="09:00",
            rule="weekly", until_date=date(2026, 6, 22), manager_id=mid)
        with db._connect() as conn:
            cur = db._exec(conn,
                "SELECT COUNT(*) FROM events WHERE manager_id = ?", (mid,))
            count = cur.fetchone()[0]
        assert count == 4, f"truncated weekly should be 4 events, got {count}"

    def test_unknown_rule_raises(self):
        from datetime import date
        try:
            db.create_recurring_events(
                title="x", event_type="check_in",
                start_date=date(2026, 6, 1), scheduled_time="09:00",
                rule="bogus", manager_id=1)
        except ValueError:
            pass
        else:
            raise AssertionError("unknown rule must raise ValueError")

    def test_iso_string_start_raises_typeerror(self):
        """Defense against a stringly-typed callsite that would silently
        TypeError inside _add_months_anchored. Form passes a date object."""
        try:
            db.create_recurring_events(
                title="x", event_type="check_in",
                start_date="2026-06-01", scheduled_time="09:00",
                rule="weekly", manager_id=1)
        except TypeError:
            pass
        else:
            raise AssertionError("iso string start_date must raise TypeError")


class TestExpiryWarning:
    """find_expiring_recurring_series + stamp_recurrence_warning drive the
    expiry-warning branch in next_step_for. Materialized series silently
    end after 12 occurrences without this warning surfacing."""

    def test_no_series_returns_none(self):
        mid = db.create_manager("exp_mgr1", "Exp1", "pass1234")
        assert db.find_expiring_recurring_series(manager_id=mid) is None

    def test_fires_when_latest_child_within_lead(self):
        from datetime import date, timedelta
        mid = db.create_manager("exp_mgr2", "Exp2", "pass1234")
        member = db.add_team_member("ExpMember", manager_id=mid)
        # Series whose latest child is 7 days out — well within the
        # default 14-day lead window.
        start = date.today() - timedelta(days=70)  # weekly × 11 = 77 days ago
        db.create_recurring_events(
            title="Soon-Expiring", event_type="one_on_one",
            start_date=start, scheduled_time="10:00",
            rule="weekly", team_member_id=member, manager_id=mid)
        result = db.find_expiring_recurring_series(manager_id=mid)
        assert result is not None
        assert result.get("title") == "Soon-Expiring"
        assert result.get("recurrence_rule") == "weekly"

    def test_does_not_fire_when_latest_child_too_far_out(self):
        from datetime import date
        mid = db.create_manager("exp_mgr3", "Exp3", "pass1234")
        member = db.add_team_member("FarMember", manager_id=mid)
        # Series starting today — latest child is ~11 weeks out, beyond
        # the 14-day lead.
        db.create_recurring_events(
            title="Plenty of Time", event_type="one_on_one",
            start_date=date.today(), scheduled_time="10:00",
            rule="weekly", team_member_id=member, manager_id=mid)
        assert db.find_expiring_recurring_series(manager_id=mid) is None

    def test_warning_stamp_suppresses_until_grace_expires(self):
        from datetime import date, timedelta
        mid = db.create_manager("exp_mgr4", "Exp4", "pass1234")
        member = db.add_team_member("StampMember", manager_id=mid)
        start = date.today() - timedelta(days=70)
        pid = db.create_recurring_events(
            title="StampSeries", event_type="one_on_one",
            start_date=start, scheduled_time="10:00",
            rule="weekly", team_member_id=member, manager_id=mid)
        # First detection succeeds.
        first = db.find_expiring_recurring_series(manager_id=mid)
        assert first is not None
        # Stamp every child in the series.
        db.stamp_recurrence_warning(manager_id=mid, series_id=pid)
        # Within the 7-day grace period, the series is suppressed.
        again = db.find_expiring_recurring_series(manager_id=mid)
        assert again is None, "stamped series must be suppressed within grace window"

    def test_parent_delete_leaves_children(self):
        """ON DELETE SET NULL on PG; on SQLite the FK isn't enforced so the
        column simply retains the no-longer-existing parent's id. Either
        way, children survive."""
        from datetime import date
        mid = db.create_manager("exp_mgr5", "Exp5", "pass1234")
        pid = db.create_recurring_events(
            title="ParentDelete", event_type="check_in",
            start_date=date(2026, 6, 1), scheduled_time="10:00",
            rule="weekly", manager_id=mid)
        with db._connect() as conn:
            db._exec(conn, "DELETE FROM events WHERE id = ?", (pid,))
            db._commit(conn)
            cur = db._exec(conn,
                "SELECT COUNT(*) FROM events WHERE manager_id = ?", (mid,))
            count = cur.fetchone()[0]
        # 11 children survive (parent gone, total is 11 not 12).
        assert count == 11, f"children must survive parent delete; got {count} rows"


class TestSessionLockHelper:
    """is_session_locked is a pure function with an injectable `now` so the
    24-hour lock contract is unit-testable without freezegun. Boundary is
    `>=` (a session created exactly LOCK_WINDOW ago is locked)."""

    def test_none_created_at_is_unlocked(self):
        assert db.is_session_locked(None) is False
        assert db.is_session_locked("") is False

    def test_within_window_is_unlocked(self):
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)
        created = now - timedelta(hours=23, minutes=59, seconds=59)
        assert db.is_session_locked(created, now=now) is False

    def test_exactly_24h_is_locked(self):
        """Boundary: >= LOCK_WINDOW. A session created exactly 24h ago IS
        locked. Off-by-one would let edits slip through on the boundary."""
        from datetime import datetime, timezone
        now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)
        created = datetime(2026, 5, 2, 12, 0, 0, tzinfo=timezone.utc)
        assert db.is_session_locked(created, now=now) is True

    def test_after_24h_is_locked(self):
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)
        created = now - timedelta(hours=24, minutes=0, seconds=1)
        assert db.is_session_locked(created, now=now) is True

    def test_iso_string_created_at(self):
        """SQLite path returns ISO strings via _normalize_row; helper must
        coerce to datetime."""
        from datetime import datetime, timezone
        now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)
        # 25h ago in ISO form — naive (SQLite doesn't store TZ info).
        assert db.is_session_locked("2026-05-02T11:00:00", now=now) is True

    def test_naive_created_at_assumed_utc(self):
        """Naive datetime (no tzinfo) is interpreted as server UTC."""
        from datetime import datetime, timezone
        now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)
        created_naive = datetime(2026, 5, 2, 11, 59, 59)  # 24h+1s ago, naive
        assert db.is_session_locked(created_naive, now=now) is True


class TestOneOnOneSessions:
    """create / read / list / update + UPSERT + cross-tenant + lock guard +
    event_id consistency. Migration idempotency via re-run."""

    def _setup(self, suffix=""):
        mid = db.create_manager(f"o3_mgr{suffix}", f"O3{suffix}", "pass1234")
        member_id = db.add_team_member(f"O3 Member{suffix}", manager_id=mid)
        return mid, member_id

    def test_create_and_read_roundtrip(self):
        mid, member_id = self._setup()
        sid = db.create_one_on_one_session(
            manager_id=mid, team_member_id=member_id,
            session_date="2026-05-03",
            direct_notes="Their week was good",
            manager_notes="Praise progress",
            followup_notes="Schedule promotion review")
        row = db.get_one_on_one_session(sid, manager_id=mid)
        assert row is not None
        assert row["direct_notes"] == "Their week was good"
        assert row["followup_notes"] == "Schedule promotion review"

    def test_rejects_none_manager(self):
        try:
            db.create_one_on_one_session(
                manager_id=None, team_member_id=1, session_date="2026-05-03")
        except ValueError:
            pass
        else:
            raise AssertionError("manager_id=None must raise")
        try:
            db.get_one_on_one_session(1, manager_id=None)
        except ValueError:
            pass
        else:
            raise AssertionError("get with manager_id=None must raise")
        try:
            db.list_one_on_one_sessions(manager_id=None)
        except ValueError:
            pass
        else:
            raise AssertionError("list with manager_id=None must raise")

    def test_upsert_on_same_date_updates_in_place(self):
        """Double-click on Save must UPDATE the existing row, not create
        a duplicate. The unique constraint enforces this; the helper's
        ON CONFLICT clause handles the conflict."""
        mid, member_id = self._setup()
        sid1 = db.create_one_on_one_session(
            manager_id=mid, team_member_id=member_id,
            session_date="2026-05-03", direct_notes="v1")
        sid2 = db.create_one_on_one_session(
            manager_id=mid, team_member_id=member_id,
            session_date="2026-05-03", direct_notes="v2")
        assert sid1 == sid2, "UPSERT should target the same row"
        row = db.get_one_on_one_session(sid1, manager_id=mid)
        assert row["direct_notes"] == "v2"
        # And the count is exactly one row for this tuple.
        rows = db.list_one_on_one_sessions(
            manager_id=mid, team_member_id=member_id)
        assert len(rows) == 1

    def test_cross_tenant_isolation(self):
        mid_a, member_a = self._setup("_a")
        mid_b, member_b = self._setup("_b")
        sid_a = db.create_one_on_one_session(
            manager_id=mid_a, team_member_id=member_a,
            session_date="2026-05-03", direct_notes="A's notes")
        sid_b = db.create_one_on_one_session(
            manager_id=mid_b, team_member_id=member_b,
            session_date="2026-05-03", direct_notes="B's notes")

        # Manager A cannot fetch B's session even with the right id.
        assert db.get_one_on_one_session(sid_b, manager_id=mid_a) is None
        # Manager B cannot fetch A's session.
        assert db.get_one_on_one_session(sid_a, manager_id=mid_b) is None
        # list scoped — A sees only A's; B sees only B's.
        a_list = db.list_one_on_one_sessions(manager_id=mid_a)
        b_list = db.list_one_on_one_sessions(manager_id=mid_b)
        assert {r["direct_notes"] for r in a_list} == {"A's notes"}
        assert {r["direct_notes"] for r in b_list} == {"B's notes"}

    def test_event_id_consistency_check_rejects_wrong_member(self):
        mid, member_a = self._setup("_evA")
        member_b = db.add_team_member("Other Member", manager_id=mid)
        eid = db.create_event("1:1 with A", "one_on_one",
                              "2026-05-03", "10:00",
                              team_member_id=member_a, manager_id=mid)

        # Binding a session for member_b to member_a's event must raise.
        try:
            db.create_one_on_one_session(
                manager_id=mid, team_member_id=member_b,
                session_date="2026-05-03", event_id=eid)
        except ValueError as e:
            assert "different member" in str(e) or "team_member" in str(e)
        else:
            raise AssertionError(
                "event_id pointing at a different member's event must raise")

    def test_event_id_consistency_check_accepts_matching_member(self):
        mid, member_id = self._setup()
        eid = db.create_event("Weekly 1:1", "one_on_one",
                              "2026-05-03", "10:00",
                              team_member_id=member_id, manager_id=mid)
        # Matching event_id must succeed.
        sid = db.create_one_on_one_session(
            manager_id=mid, team_member_id=member_id,
            session_date="2026-05-03", event_id=eid)
        assert sid is not None

    def test_update_within_window_succeeds(self):
        mid, member_id = self._setup()
        sid = db.create_one_on_one_session(
            manager_id=mid, team_member_id=member_id,
            session_date="2026-05-03", direct_notes="initial")
        # Just-created → editable.
        db.update_one_on_one_session(sid, manager_id=mid,
                                     direct_notes="updated")
        row = db.get_one_on_one_session(sid, manager_id=mid)
        assert row["direct_notes"] == "updated"

    def test_update_after_lock_raises(self):
        """Backdate created_at to >24h ago via raw UPDATE, then assert the
        helper refuses to mutate. This is the server-side guard — not a
        UI concern."""
        mid, member_id = self._setup()
        sid = db.create_one_on_one_session(
            manager_id=mid, team_member_id=member_id,
            session_date="2026-05-03", direct_notes="initial")
        # Backdate (raw — only allowed in tests).
        with db._connect() as conn:
            db._exec(conn,
                "UPDATE one_on_one_sessions "
                "SET created_at = datetime('now', '-25 hours') "
                "WHERE id = ?",
                (sid,))
            db._commit(conn)
        # Helper must refuse.
        try:
            db.update_one_on_one_session(sid, manager_id=mid,
                                         direct_notes="too late")
        except PermissionError:
            pass
        else:
            raise AssertionError(
                "update after LOCK_WINDOW must raise PermissionError")

    def test_get_most_recent_one_on_one(self):
        mid, member_id = self._setup()
        # Two sessions, different dates.
        db.create_one_on_one_session(
            manager_id=mid, team_member_id=member_id,
            session_date="2026-05-01", direct_notes="older")
        db.create_one_on_one_session(
            manager_id=mid, team_member_id=member_id,
            session_date="2026-05-08", direct_notes="newer")
        latest = db.get_most_recent_one_on_one(
            manager_id=mid, team_member_id=member_id)
        assert latest is not None
        assert latest["direct_notes"] == "newer"

    def test_get_most_recent_returns_none_when_no_sessions(self):
        mid, member_id = self._setup()
        assert db.get_most_recent_one_on_one(
            manager_id=mid, team_member_id=member_id) is None

    def test_list_filters_by_team_member(self):
        mid, member_a = self._setup("_lA")
        member_b = db.add_team_member("LB", manager_id=mid)
        db.create_one_on_one_session(
            manager_id=mid, team_member_id=member_a,
            session_date="2026-05-03", direct_notes="A note")
        db.create_one_on_one_session(
            manager_id=mid, team_member_id=member_b,
            session_date="2026-05-03", direct_notes="B note")
        a_only = db.list_one_on_one_sessions(
            manager_id=mid, team_member_id=member_a)
        assert len(a_only) == 1
        assert a_only[0]["direct_notes"] == "A note"

    def test_member_timeline_includes_one_on_one_sessions(self):
        """get_member_timeline must surface 1:1 sessions as type='one_on_one'
        so the existing activity-timeline UI under Team detail shows them
        without a separate widget."""
        mid, member_id = self._setup()
        db.create_one_on_one_session(
            manager_id=mid, team_member_id=member_id,
            session_date="2026-05-03",
            direct_notes="Their content goes here")
        timeline = db.get_member_timeline(member_id, manager_id=mid)
        types = [r["type"] for r in timeline]
        assert "one_on_one" in types
        oo = next(r for r in timeline if r["type"] == "one_on_one")
        assert oo["date"] == "2026-05-03"
        assert oo["summary"] == "One-on-one"

    def test_migration_idempotent(self):
        """0010_one_on_one_sessions: re-running migrations is a no-op
        (CREATE TABLE IF NOT EXISTS + indexes are idempotent)."""
        mid, _ = self._setup()
        with db._connect() as conn:
            db._run_migrations(conn)  # second run
        # Sanity: table exists and has the expected columns.
        with db._connect() as conn:
            cur = db._exec(conn, "PRAGMA table_info(one_on_one_sessions)")
            cols = [r[1] for r in cur.fetchall()]
        for required in ("manager_id", "team_member_id", "event_id",
                         "session_date", "direct_notes", "manager_notes",
                         "followup_notes", "created_at", "updated_at"):
            assert required in cols, \
                f"0010 migration must add {required} to one_on_one_sessions"



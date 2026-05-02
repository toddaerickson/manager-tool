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
        did = db.add_delegation(task="Write docs", manager_id=mid)
        db.update_delegation(did, manager_id=mid, status="completed")

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

        db.init_db()  # Should apply 0002 again, which backfills the NULL row.

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
        did = db.add_delegation(task="Original task", team_member_id=t1, manager_id=m1)

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


class TestProductionFallbackGate:
    """Regression for AUDIT H5 / P2.5 — SQLite fallback is disabled when
    MANAGER_TOOL_ENV=prod; outside production the fallback behaves as before."""

    def _force_pg_failure(self, monkeypatch):
        """Pretend Postgres is configured and raises on connect.

        We deliberately do NOT monkeypatch _detect_pg — the real implementation
        consults _USE_PG, which the fallback path flips to False, so the next
        get_connection() call naturally takes the SQLite branch."""
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
        """In prod, a Postgres outage must raise — never fall through to
        SQLite (which would silently route writes to ephemeral storage)."""
        monkeypatch.setenv("MANAGER_TOOL_ENV", "prod")
        self._force_pg_failure(monkeypatch)
        try:
            db.get_connection()
        except db.DatabaseUnavailableError:
            return
        raise AssertionError(
            "get_connection must raise DatabaseUnavailableError in prod "
            "when Postgres is unreachable; SQLite fallback is disallowed"
        )

    def test_non_prod_falls_back_to_sqlite(self, monkeypatch):
        """Outside prod, the fallback still kicks in so dev laptops and CI
        keep working when Postgres isn't configured."""
        monkeypatch.delenv("MANAGER_TOOL_ENV", raising=False)
        self._force_pg_failure(monkeypatch)
        # Should NOT raise — should fall through to SQLite.
        conn = db.get_connection()
        try:
            row = conn.execute("SELECT 1").fetchone()
            assert row is not None
        finally:
            conn.close()

    def test_pg_failed_flags_set_after_outage(self, monkeypatch):
        """The status flags consumed by the UI are set in both prod and dev
        outage paths — operators see the failure either way."""
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

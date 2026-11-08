"""PR2: real names never reach the AI provider.

Unit tests for coaching/privacy.py, plus end-to-end tests that drive each
of the six AI call sites with a fake client and inspect the exact request
body that would have been sent.
"""

import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from coaching import privacy, services
from coaching.privacy import Pseudonymizer
from core.models import (
    ActionItem, CareerConversation, Decision, Delegation, Feedback, Goal,
    JournalEntry, KnownPerson, Manager, OneOnOneSession, RunningNote,
    TeamMember,
)

TOKEN = re.compile(r"⟦P\d+⟧")


def _p(*names):
    return Pseudonymizer([(n, [n]) for n in names])


# ---------------------------------------------------------------- unit


class TestMatching:
    def test_full_first_last_and_possessive(self):
        p = _p("Sarah Chen")
        out = p.redact("Sarah Chen said Sarah's plan beats Chen's.")
        assert "Sarah" not in out and "Chen" not in out
        assert out.count("⟦P1⟧") == 3  # "Sarah Chen", "Sarah", "Chen"

    def test_case_insensitive_for_ordinary_names(self):
        out = _p("Sarah Chen").redact("lunch with sarah, then CHEN")
        assert "sarah" not in out.lower() and "chen" not in out.lower()

    def test_hyphenated_and_multi_part(self):
        p = _p("Mary-Jane Juan Carlos de la Cruz")
        out = p.redact("Mary and Jane; Carlos met de la Cruz. Mary-Jane too.")
        for leak in ("Mary", "Jane", "Carlos", "Cruz"):
            assert leak not in out
        assert "de la" not in out  # consumed inside the "de la Cruz" run

    def test_particles_never_matched_alone(self):
        out = _p("Ana de la Cruz").redact("the cost de facto")
        assert out == "the cost de facto"

    def test_underscore_dot_and_email_forms(self):
        p = _p("Sarah Chen")
        out = p.redact("see sarah_chen, sarah.chen@corp.com and schen@corp.com")
        assert "sarah" not in out.lower() and "chen" not in out.lower()

    def test_accents_and_curly_apostrophes(self):
        out = _p("José O'Brien").redact("Jose met O’Brien and josé")
        assert "Jos" not in out and "Brien" not in out

    def test_accented_note_matches_plain_stored_name(self):
        out = _p("Zoe Muller").redact("Zoë Müller")
        assert "Zo" not in out and "ller" not in out

    def test_common_word_name_only_when_capitalised(self):
        p = _p("Will Smith")
        assert p.redact("I will follow up") == "I will follow up"
        assert "Will" not in p.redact("Will said yes")
        assert "Smith" not in p.redact("ask smith")  # 'smith' is listed -> case-sensitive
        assert "smith" in p.redact("ask smith")

    def test_unlisted_word_errs_toward_hiding(self):
        # 'Brooke' is not on the common-word list: any case is replaced.
        assert "brooke" not in _p("Brooke Ng").redact("by the brooke").lower()

    def test_word_boundaries_hold(self):
        p = _p("Ann Lee")
        assert p.redact("Annual planning, Leeds office") == "Annual planning, Leeds office"

    def test_longest_match_wins(self):
        p = Pseudonymizer([("Sarah Chen", ["Sarah Chen"]), ("Sarah Park", ["Sarah Park"])])
        out = p.redact("Sarah Chen and Sarah Park")
        a, b = TOKEN.findall(out)
        assert a != b

    def test_nicknames(self):
        p = Pseudonymizer([("Robert Lang", ["Robert Lang", "Bob", "Bobby"])])
        assert "Bob" not in p.redact("Bobby and Bob")

    def test_typed_token_cannot_be_restored_into_a_name(self):
        p = _p("Sarah Chen")
        sent = p.redact("⟦P1⟧ is fired")
        assert "⟦P1⟧" not in sent
        assert "Sarah" not in p.restore(sent)


class TestRestore:
    def test_round_trip(self):
        p = _p("Sarah Chen")
        sent = p.redact("Sarah missed the deadline")
        assert p.restore(sent) == "Sarah Chen missed the deadline"

    def test_restore_leaves_ordinary_text_alone(self):
        p = _p("Sarah Chen")
        text = "Your direct 1:1 with Direct-12 and P1 is due."
        assert p.restore(text) == text

    def test_unknown_token_left_visible(self):
        assert _p("Sarah Chen").restore("ask ⟦P9⟧") == "ask ⟦P9⟧"


class TestRandomTokens:
    def test_tokens_shuffle_across_requests(self):
        people = [(f"Person{i} Zed{i}", [f"Person{i} Zed{i}"]) for i in range(12)]
        seen = set()
        for _ in range(20):
            seen.add(Pseudonymizer(people).redact("Person0 Zed0"))
        assert len(seen) > 1  # P(all 20 equal by chance) = 12**-19


class TestNoBypass:
    def test_sdk_only_touched_in_llm_module(self):
        root = Path(__file__).resolve().parent.parent
        offenders = []
        for path in root.rglob("*.py"):
            rel = path.relative_to(root).as_posix()
            if rel.startswith(".venv/") or rel == "coaching/llm.py" or "test" in path.name:
                continue
            src = path.read_text(encoding="utf-8")
            if re.search(r"\bAnthropic\w*\(|\.messages\.(create|stream|count_tokens|batches|parse)\b|beta\.messages", src):
                offenders.append(rel)
        assert offenders == []


# ---------------------------------------------------------- end to end


class _FakeClient:
    """Records every request; replies with `reply_fn(sent_user_message)`."""

    def __init__(self, reply_fn=None):
        self.sent = []
        outer = self

        class _Messages:
            def create(self, **kwargs):
                outer.sent.append(kwargs)
                text = (reply_fn or (lambda m: "ok"))(kwargs["messages"][0]["content"])
                block = type("B", (), {"text": text})()
                return type("M", (), {"content": [block]})()

        self.messages = _Messages()

    def body(self):
        return json.dumps(self.sent, ensure_ascii=False)


SECRET_NAMES = ["Sarah", "Chen", "Bobby", "Margaret", "Okafor", "Diane",
                "Whitfield", "Jose", "José"]


@pytest.fixture
def world(db, monkeypatch):
    m = Manager.objects.create(username="priv", display_name="P",
                               password_hash="x", email="priv@example.com")
    sarah = TeamMember.objects.create(manager_id=m.id, name="Sarah Chen",
                                      aliases="Bobby")
    TeamMember.objects.create(manager_id=m.id, name="José Okafor",
                              deleted_at=timezone.now())   # soft-deleted
    KnownPerson.objects.create(manager_id=m.id, name="Margaret Okafor",
                               source="former_direct")
    KnownPerson.objects.create(manager_id=m.id, name="Diane Whitfield")  # the boss
    today = date.today()
    mention = ("Sarah Chen (Bobby) told Diane Whitfield that Margaret and "
               "Jose left; sarah.chen@corp.com")
    JournalEntry.objects.create(manager_id=m.id, entry_date=today.isoformat(),
                                entry_type="daily", content=mention, mood=2)
    Delegation.objects.create(manager_id=m.id, team_member=sarah, task=mention,
                              status="active",
                              check_in_date=(today - timedelta(days=2)).isoformat())
    Decision.objects.create(manager_id=m.id, title=mention, status="active",
                            review_date=today.isoformat())
    Goal.objects.create(manager_id=m.id, team_member=sarah, quarter="Q4 2026",
                        description=mention, status="in_progress")
    ActionItem.objects.create(manager_id=m.id, description=mention, status="pending",
                              due_date=(today - timedelta(days=1)).isoformat())
    Feedback.objects.create(manager_id=m.id, team_member=sarah,
                            feedback_type="constructive", behavior=mention,
                            impact=mention, created_at=timezone.now())
    RunningNote.objects.create(manager_id=m.id, team_member=sarah,
                               note_date=today.isoformat(), content=mention)
    CareerConversation.objects.create(manager_id=m.id, team_member=sarah,
                                      conversation_date="2026-11-01",
                                      topic=mention, notes=mention)
    prev = OneOnOneSession.objects.create(manager_id=m.id, team_member=sarah,
                                          session_date="2026-10-01",
                                          status="completed",
                                          manager_notes=mention,
                                          followup_notes=mention)
    OneOnOneSession.objects.create(manager_id=m.id, team_member=sarah,
                                   session_date="2026-11-02", status="completed",
                                   manager_notes=mention)
    upcoming = OneOnOneSession.objects.create(manager_id=m.id, team_member=sarah,
                                              session_date=today.isoformat(),
                                              status="draft")
    fake = _FakeClient(reply_fn=lambda msg: " ".join(TOKEN.findall(msg)) or "none")
    monkeypatch.setattr(services, "_get_client", lambda mid: fake)
    return {"m": m, "sarah": sarah, "fake": fake, "prev": prev,
            "upcoming": upcoming}


def _assert_clean(fake):
    assert fake.sent, "no request was made"
    body = fake.body()
    for name in SECRET_NAMES:
        assert name.lower() not in body.lower(), f"{name!r} leaked"
    assert TOKEN.search(body)


class TestNoNameLeavesAnyCallSite:
    def test_journal_coaching(self, world):
        out = services.get_coaching_response(
            JournalEntry.objects.first().content, world["m"].id)
        _assert_clean(world["fake"])
        assert "Sarah Chen" in out  # restored for the manager

    def test_daily_coach(self, world):
        services.generate_ai_suggestion(world["m"].id)
        _assert_clean(world["fake"])

    def test_weekly_plan(self, world):
        services.generate_weekly_plan(world["m"].id)
        _assert_clean(world["fake"])

    def test_prep_brief(self, world):
        out = services.generate_prep_brief(world["upcoming"].id, world["m"].id)
        _assert_clean(world["fake"])
        assert "Sarah Chen" in out

    def test_sbi_draft(self, world):
        services.draft_sbi("Bobby talked over Diane Whitfield", world["m"].id,
                           member_name="Sarah Chen", feedback_type="constructive")
        _assert_clean(world["fake"])

    def test_quarterly_review(self, world):
        services.generate_quarterly_review(world["sarah"].id, world["m"].id, "Q4 2026")
        _assert_clean(world["fake"])

    def test_system_prompt_carries_placeholder_rule(self, world):
        services.generate_ai_suggestion(world["m"].id)
        assert "⟦P3⟧" in world["fake"].sent[0]["system"]

    def test_pseudonymizer_inactive_after_request(self, world):
        services.generate_ai_suggestion(world["m"].id)
        assert privacy.active() is None
        assert services._sanitize_user_text("Sarah") == "Sarah"


class TestPurgeTombstone:
    def test_purge_keeps_name_on_hidden_list(self, db):
        from django.core.management import call_command

        m = Manager.objects.create(username="purge", display_name="P",
                                   password_hash="x", email="purge@example.com")
        TeamMember.objects.create(manager_id=m.id, name="Old Direct", aliases="OD",
                                  deleted_at=timezone.now() - timedelta(days=40))
        call_command("purge_deleted_team_members")
        assert not TeamMember.objects.filter(name="Old Direct").exists()
        kp = KnownPerson.objects.get(manager_id=m.id, name="Old Direct")
        assert kp.source == "former_direct" and kp.aliases == "OD"
        names = [n for _d, ns in privacy.people_for_manager(m.id) for n in ns]
        assert "Old Direct" in names

    def test_purge_rolls_back_if_tombstone_fails(self, db, monkeypatch):
        from django.core.management import call_command
        from core.management.commands import purge_deleted_team_members as cmd

        m = Manager.objects.create(username="purge2", display_name="P",
                                   password_hash="x", email="purge2@example.com")
        TeamMember.objects.create(manager_id=m.id, name="Kept Direct",
                                  deleted_at=timezone.now() - timedelta(days=40))

        def boom(_tm):
            raise RuntimeError("tombstone write failed")

        monkeypatch.setattr(cmd, "tombstone_former_direct", boom)
        with pytest.raises(RuntimeError):
            call_command("purge_deleted_team_members")
        assert TeamMember.objects.filter(name="Kept Direct").exists()


class TestHiddenNamesPage:
    def _login(self, client):
        from django.contrib.auth import get_user_model

        m = Manager.objects.create(username="hn", display_name="H",
                                   password_hash="x", email="hn@example.com")
        user = get_user_model().objects.create_user("hn", "hn@example.com", "pw")
        client.force_login(user)
        return m

    def test_add_list_delete(self, client, db):
        m = self._login(client)
        resp = client.post("/team/hidden-names/add/",
                           {"name": "Diane Whitfield", "aliases": "Di"})
        assert resp.status_code == 302
        kp = KnownPerson.objects.get(manager_id=m.id)
        assert "Diane Whitfield" in client.get("/team/").content.decode()
        client.post(f"/team/hidden-names/{kp.id}/delete/")
        assert not KnownPerson.objects.filter(pk=kp.id).exists()

    def test_cross_tenant_delete_is_404(self, client, db):
        self._login(client)
        other = KnownPerson.objects.create(manager_id=999999, name="Not Yours")
        resp = client.post(f"/team/hidden-names/{other.id}/delete/")
        assert resp.status_code == 404
        assert KnownPerson.objects.filter(pk=other.id).exists()

    def test_member_nickname_saved(self, client, db):
        m = self._login(client)
        client.post("/team/add/", {"name": "Robert Lang", "aliases": "Bob"})
        assert TeamMember.objects.get(manager_id=m.id).aliases == "Bob"


class TestCapCannotSplitAName:
    def test_name_straddling_the_200_char_cap(self, db, monkeypatch):
        m = Manager.objects.create(username="cap", display_name="C",
                                   password_hash="x", email="cap@example.com")
        TeamMember.objects.create(manager_id=m.id, name="Christopher Lang")
        # The daily coach caps each journal entry at 200 chars. Capping
        # first would leave "Christop" — an unmatched prefix that leaks.
        JournalEntry.objects.create(manager_id=m.id, entry_date=date.today().isoformat(),
                                    entry_type="daily", content="x" * 192 + " Christopher")
        fake = _FakeClient()
        monkeypatch.setattr(services, "_get_client", lambda mid: fake)
        services.generate_ai_suggestion(m.id)
        assert "christ" not in fake.body().lower()

"""Django models for the manager-tool migration target.

Generated from `python manage.py inspectdb` against the Neon dev branch
after applying scripts/migrate_p2_config_to_id_pk.sql, then hand-cleaned:

- TenantManager wired on every tenant-scoped model (audit C1 parity).
- `managed = False` on legacy auth tables (Session, LoginAttempt) — they
  exist in DB but django-allauth replaces them; dropped in Phase 8.
- `managed = False` on Streamlit's schema_migrations ledger — Django uses
  django_migrations instead; the old ledger stays as a frozen artifact.
- ON DELETE behavior matches the actual PG constraints:
  * NO ACTION → models.DO_NOTHING (the DB enforces; Django stays out)
  * ON DELETE SET NULL → models.SET_NULL  (events.parent_event_id and
    one_on_one_sessions.event_id only)
- Meta.indexes mirrors M5's hot-path btree indexes (queried from
  pg_indexes); the partial index on events(manager_id, parent_event_id)
  uses Django's `condition=Q(parent_event_id__isnull=False)`.
  NOTE: Django enforces a 30-char index-name limit (Oracle compat) so a
  few names here are shortened vs. the actual DB names. Since Phase 2
  fake-applies the initial migration, Django never tries to create or
  rename these indexes — the short names exist only in Django's
  migration ledger; the long-named DB indexes do the real work at
  query time. Phase 8 cleanup may align them.

Date-shape note: TextField on `*_date` columns reflects how the data is
stored (TEXT 'YYYY-MM-DD' on both backends — see CLAUDE.md). DO NOT
auto-convert to DateField; the per-PR date-shape grep check exists to
catch helpers that assume string dates.
"""

from django.db import models
from django.db.models import Q

from .managers import TenantManager


# ============================================================
# Auth-adjacent (Manager keeps real data; allauth replaces the rest)
# ============================================================


class Manager(models.Model):
    username = models.TextField(unique=True)
    display_name = models.TextField()
    email = models.TextField(blank=True, null=True)
    password_hash = models.TextField()
    work_schedule = models.TextField(blank=True, null=True)
    timezone = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "managers"


class User(models.Model):
    """Google OAuth user records (distinct from Manager — Phase 3 will
    decide the bridging strategy with django-allauth)."""

    google_id = models.TextField(unique=True)
    email = models.TextField()
    name = models.TextField(blank=True, null=True)
    picture = models.TextField(blank=True, null=True)
    last_login = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "users"


class Session(models.Model):
    """Streamlit's server-side session table (audit H2). django-allauth's
    built-in session handling replaces this; table dropped in Phase 8."""

    id = models.TextField(primary_key=True)
    manager = models.ForeignKey(Manager, models.DO_NOTHING)
    created_at = models.DateTimeField(blank=True, null=True)
    last_seen = models.DateTimeField(blank=True, null=True)
    expires_at = models.DateTimeField()
    user_agent_hash = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = "sessions"


class LoginAttempt(models.Model):
    """Streamlit's persistent rate-limit table (audit H3). django-allauth's
    built-in rate limiting replaces this; table dropped in Phase 8."""

    username = models.TextField(primary_key=True)
    failed_count = models.IntegerField()
    last_attempt_at = models.DateTimeField()
    locked_until = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = "login_attempts"


class SchemaMigration(models.Model):
    """Streamlit's migration ledger. Django uses django_migrations; this
    stays as a frozen artifact so re-running Streamlit (during the dual-
    run window) doesn't blow up. Decision: see manager-tool-django/README.md."""

    id = models.TextField(primary_key=True)
    applied_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = "schema_migrations"


# ============================================================
# Per-tenant config (was composite-PK; now id PK + unique_together)
# ============================================================


class Config(models.Model):
    """Per-manager key/value config. Phase 2 schema change (see
    scripts/migrate_p2_config_to_id_pk.sql) added the id PK and the
    unique index on (manager_id, key). Use `Config.objects.update_or_create(
    manager_id=X, key=Y, defaults={'value': Z})` as the upsert; matches
    the existing Streamlit `set_config()` semantics."""

    id = models.BigAutoField(primary_key=True)
    manager_id = models.IntegerField(db_index=True)
    key = models.TextField()
    value = models.TextField(blank=True, null=True)

    objects = TenantManager()

    class Meta:
        db_table = "config"
        unique_together = (("manager_id", "key"),)


# ============================================================
# Tenant-scoped data (every model below uses TenantManager)
# ============================================================


class TeamMemberManager(TenantManager):
    """Adds soft-delete-aware queries on top of TenantManager.

    `for_manager(X)` keeps its original semantic (returns ALL rows for
    the manager, including soft-deleted) so existing callers and
    cross-tenant tests don't change shape. Use `active_for_manager(X)`
    in views/services that should never see deleted rows; use
    `recently_deleted_for_manager(X)` for the 30-day undo UI.
    """

    UNDO_WINDOW_DAYS = 30

    def active_for_manager(self, manager_id):
        return self.for_manager(manager_id).filter(deleted_at__isnull=True)

    def recently_deleted_for_manager(self, manager_id):
        from django.utils import timezone
        from datetime import timedelta
        cutoff = timezone.now() - timedelta(days=self.UNDO_WINDOW_DAYS)
        return (
            self.for_manager(manager_id)
            .filter(deleted_at__isnull=False, deleted_at__gte=cutoff)
            .order_by("-deleted_at")
        )


class TeamMember(models.Model):
    manager_id = models.IntegerField(blank=True, null=True, db_index=True)
    name = models.TextField()
    email = models.TextField(blank=True, null=True)
    role = models.TextField(blank=True, null=True)
    start_date = models.TextField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)
    # Soft-delete with 30-day undo window. NULL = active. Streamlit
    # doesn't know about this column (it's Django-only); during the
    # dual-run window Streamlit may show soft-deleted members.
    # Hard-delete after the undo window happens via a management command
    # wired to a Render Cron in Phase 6.
    deleted_at = models.DateTimeField(blank=True, null=True, db_index=True)

    objects = TeamMemberManager()

    def __str__(self):
        # Used by ModelChoiceField in dropdowns (EventForm participant
        # picker); without this, options render as "TeamMember object (1)".
        return self.name

    class Meta:
        db_table = "team_members"
        indexes = [
            models.Index(fields=["manager_id"], name="ix_team_members_manager"),
        ]


class Event(models.Model):
    manager_id = models.IntegerField(blank=True, null=True, db_index=True)
    title = models.TextField()
    event_type = models.TextField()
    team_member = models.ForeignKey(TeamMember, models.DO_NOTHING, blank=True, null=True)
    scheduled_date = models.TextField()
    scheduled_time = models.TextField()
    duration_minutes = models.IntegerField(blank=True, null=True)
    location = models.TextField(blank=True, null=True)
    agenda = models.TextField(blank=True, null=True)
    status = models.TextField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    calendar_invite_sent = models.IntegerField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)
    # Recurring events (audit PR 4): parent_event has ON DELETE SET NULL
    recurrence_rule = models.TextField(blank=True, null=True)
    parent_event = models.ForeignKey("self", models.SET_NULL, blank=True, null=True)
    recurrence_warned_at = models.TextField(blank=True, null=True)

    objects = TenantManager()

    def __str__(self):
        # Used by ModelChoiceField in dropdowns (e.g., the To Do form's
        # "Related event" picker). Without this, options render as
        # "Event object (1)".
        date = self.scheduled_date or "?"
        title = self.title or "(untitled)"
        return f"{title} ({date})"

    class Meta:
        db_table = "events"
        indexes = [
            models.Index(
                fields=["manager_id", "scheduled_date", "status"],
                name="ix_events_manager_date_status",
            ),
            # Partial index — only rows where parent_event_id IS NOT NULL.
            models.Index(
                fields=["manager_id", "parent_event"],
                name="ix_events_manager_parent",
                condition=Q(parent_event__isnull=False),
            ),
            models.Index(fields=["parent_event"], name="ix_events_parent"),
        ]


class ActionItem(models.Model):
    manager_id = models.IntegerField(blank=True, null=True, db_index=True)
    event = models.ForeignKey(Event, models.DO_NOTHING, blank=True, null=True)
    description = models.TextField()
    # `assignee` stays in DB for back-compat with existing rows but is
    # no longer exposed in the form — to-dos are the manager's own list,
    # work entrusted to a direct goes in Delegations. Dropped in Phase 8.
    assignee = models.TextField(blank=True, null=True)
    due_date = models.TextField(blank=True, null=True)
    # Optional time-of-day for due_date. NULL = date-only ("by end of
    # day"). Format "HH:MM" — same as Event.scheduled_time. Added Phase
    # 5.3.1 per user feedback.
    due_time = models.TextField(blank=True, null=True)
    status = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    objects = TenantManager()

    class Meta:
        db_table = "action_items"
        indexes = [
            models.Index(
                fields=["manager_id", "status", "due_date"],
                name="ix_action_mgr_status_due",
            ),
        ]


class OneOnOneSession(models.Model):
    manager = models.ForeignKey(Manager, models.DO_NOTHING)
    team_member = models.ForeignKey(TeamMember, models.DO_NOTHING)
    event = models.ForeignKey(Event, models.SET_NULL, blank=True, null=True)
    session_date = models.TextField()
    direct_notes = models.TextField(blank=True, null=True)
    manager_notes = models.TextField(blank=True, null=True)
    followup_notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)

    objects = TenantManager()

    class Meta:
        db_table = "one_on_one_sessions"
        unique_together = (("manager", "team_member", "session_date"),)
        indexes = [
            models.Index(fields=["manager"], name="ix_one_on_one_sessions_manager"),
            models.Index(
                fields=["team_member", "-session_date"],
                name="ix_one_on_one_member_date",
            ),
        ]


class Delegation(models.Model):
    manager_id = models.IntegerField(blank=True, null=True, db_index=True)
    team_member = models.ForeignKey(TeamMember, models.DO_NOTHING, blank=True, null=True)
    task = models.TextField()
    outcome_expected = models.TextField(blank=True, null=True)
    autonomy_level = models.TextField(blank=True, null=True)
    check_in_date = models.TextField(blank=True, null=True)
    status = models.TextField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    objects = TenantManager()

    class Meta:
        db_table = "delegations"
        indexes = [
            models.Index(
                fields=["manager_id", "status", "check_in_date"],
                name="ix_deleg_mgr_status_checkin",
            ),
        ]


class Feedback(models.Model):
    team_member = models.ForeignKey(TeamMember, models.DO_NOTHING)
    event = models.ForeignKey(Event, models.DO_NOTHING, blank=True, null=True)
    feedback_type = models.TextField()
    situation = models.TextField(blank=True, null=True)
    behavior = models.TextField(blank=True, null=True)
    impact = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    manager_id = models.IntegerField(blank=True, null=True, db_index=True)

    objects = TenantManager()

    class Meta:
        db_table = "feedback"
        indexes = [
            models.Index(
                fields=["team_member", "created_at"],
                name="ix_feedback_member_created",
            ),
        ]


class Goal(models.Model):
    team_member = models.ForeignKey(TeamMember, models.DO_NOTHING)
    quarter = models.TextField()
    description = models.TextField()
    key_results = models.TextField(blank=True, null=True)
    status = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)
    manager_id = models.IntegerField(blank=True, null=True, db_index=True)
    target_date = models.TextField(blank=True, null=True)

    objects = TenantManager()

    class Meta:
        db_table = "goals"
        indexes = [
            models.Index(
                fields=["manager_id", "target_date"],
                name="ix_goals_manager_target",
            ),
        ]


class Skill(models.Model):
    team_member = models.ForeignKey(TeamMember, models.DO_NOTHING)
    skill_name = models.TextField()
    proficiency = models.TextField(blank=True, null=True)
    is_strength = models.IntegerField(blank=True, null=True)
    is_growth_area = models.IntegerField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)
    manager_id = models.IntegerField(blank=True, null=True, db_index=True)

    objects = TenantManager()

    class Meta:
        db_table = "skills"


class DevelopmentPlan(models.Model):
    team_member = models.ForeignKey(TeamMember, models.DO_NOTHING)
    title = models.TextField()
    description = models.TextField(blank=True, null=True)
    target_date = models.TextField(blank=True, null=True)
    status = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)
    manager_id = models.IntegerField(blank=True, null=True, db_index=True)

    objects = TenantManager()

    class Meta:
        db_table = "development_plans"


class Milestone(models.Model):
    plan = models.ForeignKey(DevelopmentPlan, models.DO_NOTHING)
    description = models.TextField()
    target_date = models.TextField(blank=True, null=True)
    completed = models.IntegerField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    manager_id = models.IntegerField(blank=True, null=True, db_index=True)

    objects = TenantManager()

    class Meta:
        db_table = "milestones"


class Decision(models.Model):
    manager_id = models.IntegerField(blank=True, null=True, db_index=True)
    title = models.TextField()
    context = models.TextField(blank=True, null=True)
    alternatives = models.TextField(blank=True, null=True)
    rationale = models.TextField(blank=True, null=True)
    expected_outcome = models.TextField(blank=True, null=True)
    review_date = models.TextField(blank=True, null=True)
    status = models.TextField(blank=True, null=True)
    actual_outcome = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)

    objects = TenantManager()

    class Meta:
        db_table = "decisions"


class JournalEntry(models.Model):
    manager_id = models.IntegerField(blank=True, null=True, db_index=True)
    entry_date = models.TextField()
    entry_type = models.TextField()
    content = models.TextField(blank=True, null=True)
    mood = models.IntegerField(blank=True, null=True)
    energy = models.IntegerField(blank=True, null=True)
    private_notes = models.TextField(blank=True, null=True)
    tags = models.TextField(blank=True, null=True)
    coaching_response = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)

    objects = TenantManager()

    class Meta:
        db_table = "journal_entries"
        indexes = [
            models.Index(
                fields=["manager_id", "entry_date"],
                name="ix_journal_mgr_date",
            ),
        ]


class RunningNote(models.Model):
    manager_id = models.IntegerField(blank=True, null=True, db_index=True)
    team_member = models.ForeignKey(TeamMember, models.DO_NOTHING, blank=True, null=True)
    note_date = models.TextField()
    content = models.TextField()
    category = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)

    objects = TenantManager()

    class Meta:
        db_table = "running_notes"
        indexes = [
            models.Index(
                fields=["team_member", "note_date"],
                name="ix_running_notes_member_date",
            ),
        ]


class CareerConversation(models.Model):
    team_member = models.ForeignKey(TeamMember, models.DO_NOTHING)
    conversation_date = models.TextField()
    topic = models.TextField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    next_steps = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    manager_id = models.IntegerField(blank=True, null=True, db_index=True)

    objects = TenantManager()

    class Meta:
        db_table = "career_conversations"


class SelfAssessment(models.Model):
    manager_id = models.IntegerField(blank=True, null=True, db_index=True)
    week_date = models.TextField()
    dimension = models.TextField()
    score = models.IntegerField()
    created_at = models.DateTimeField(blank=True, null=True)

    objects = TenantManager()

    class Meta:
        db_table = "self_assessments"
        unique_together = (("manager_id", "week_date", "dimension"),)


# ============================================================
# Audit log (D3 — HR data mutation tracking)
# ============================================================


class AuditLog(models.Model):
    """Immutable log of data mutations on HR-sensitive models.

    Flagged by /review-as audit on PR #67: feedback, career dev,
    delegations, and goals contain HR-sensitive data. Any create/update/
    delete should be traceable for compliance.

    This is Django-only (no Streamlit equivalent). The table is created
    by a Django migration, not the Streamlit migration runner.
    """

    ACTION_CHOICES = [
        ("create", "Create"),
        ("update", "Update"),
        ("delete", "Delete"),
    ]

    manager_id = models.IntegerField(db_index=True)
    action = models.TextField()  # create / update / delete
    entity_type = models.TextField()  # e.g. "TeamMember", "Feedback"
    entity_id = models.IntegerField()
    summary = models.TextField()  # human-readable description
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantManager()

    class Meta:
        db_table = "audit_log"
        indexes = [
            models.Index(
                fields=["manager_id", "-created_at"],
                name="ix_audit_log_mgr_created",
            ),
            models.Index(
                fields=["entity_type", "entity_id"],
                name="ix_audit_log_entity",
            ),
        ]

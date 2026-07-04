"""Forms for the core app.

manager_id is never a form field — it's set by the view from
request.manager. start_date / scheduled_date / scheduled_time stay as
TEXT in the underlying schema (Streamlit convention, CLAUDE.md);
forms convert from native date/time input to ISO/HH:MM strings on save.
"""

import logging

from django import forms

from .models import (
    ActionItem, CareerConversation, Decision, Delegation, DevelopmentPlan,
    Event, Feedback, Goal, Manager, Milestone, OneOnOneSession, RunningNote,
    Skill, TeamMember,
)


_INPUT_CLS = (
    "mt-1 block w-full border border-slate-300 rounded "
    "px-2 py-1.5 text-sm"
)


class TeamMemberForm(forms.ModelForm):
    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": _INPUT_CLS}),
    )

    class Meta:
        model = TeamMember
        fields = ["name", "email", "role", "start_date", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Full name", "class": _INPUT_CLS}),
            "email": forms.EmailInput(attrs={"placeholder": "name@company.com", "class": _INPUT_CLS}),
            "role": forms.TextInput(attrs={"placeholder": "Engineer / PM / ...", "class": _INPUT_CLS}),
            "notes": forms.Textarea(attrs={"rows": 2, "class": _INPUT_CLS}),
        }

    def clean_start_date(self):
        d = self.cleaned_data.get("start_date")
        return d.isoformat() if d else None


# Event types match schema_postgres.sql CHECK constraint
EVENT_TYPE_CHOICES = [
    ("one_on_one", "1:1"),
    ("check_in", "Check-in"),
    ("coaching", "Coaching"),
    ("quarterly_review", "Quarterly review"),
    ("other", "Other"),
]


def _time_choices():
    """30-min increments from 6:00 AM through 9:00 PM. Stored value is
    24-hour HH:MM (matches the schema's TEXT format); display is
    12-hour with AM/PM for readability."""
    out = []
    for h in range(6, 22):  # 06..21 inclusive
        for m in (0, 30):
            value = f"{h:02d}:{m:02d}"
            display_h = ((h - 1) % 12) + 1   # 0/12 → 12, 13 → 1, etc.
            display_period = "PM" if h >= 12 else "AM"
            display = f"{display_h}:{m:02d} {display_period}"
            out.append((value, display))
    return out


TIME_CHOICES = _time_choices()


# Phase 5.2b — recurrence dropdown. Empty string = one-off event;
# non-empty value routes through core.services.events.create_recurring_events.
RECURRENCE_CHOICES = [
    ("", "Doesn't repeat"),
    ("weekly", "Weekly (up to 12)"),
    ("monthly", "Monthly (up to 12)"),
    ("quarterly", "Quarterly (up to 8)"),
]


class EventForm(forms.ModelForm):
    """Schedule a one-off OR recurring event. Recurrence rule + until
    date are form-only fields (not Meta.fields); the view branches on
    cleaned_data['recurrence_rule'] to either create one Event row or
    delegate to core.services.events.create_recurring_events."""

    scheduled_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": _INPUT_CLS}),
    )
    scheduled_time = forms.ChoiceField(
        choices=TIME_CHOICES,
        widget=forms.Select(attrs={"class": _INPUT_CLS}),
        initial="10:00",
    )
    event_type = forms.ChoiceField(
        choices=EVENT_TYPE_CHOICES,
        widget=forms.Select(attrs={"class": _INPUT_CLS}),
    )
    # Override Meta-derived field so blank title is allowed; clean()
    # fills in a default from the event_type label.
    title = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": _INPUT_CLS}),
    )

    # Phase 5.2b — form-only fields (NOT in Meta.fields):
    recurrence_rule = forms.ChoiceField(
        choices=RECURRENCE_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": _INPUT_CLS}),
    )
    until_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": _INPUT_CLS}),
        help_text="Optional cap. Series stops at the rule's max count if blank.",
    )

    class Meta:
        model = Event
        fields = [
            "title", "event_type",
            "scheduled_date", "scheduled_time",
            "team_member", "duration_minutes", "location", "agenda",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": _INPUT_CLS}),
            "team_member": forms.Select(attrs={"class": _INPUT_CLS}),
            "duration_minutes": forms.NumberInput(attrs={
                "class": _INPUT_CLS, "min": 15, "step": 15,
            }),
            "location": forms.TextInput(attrs={
                "class": _INPUT_CLS,
                "placeholder": "Office / meeting link",
            }),
            "agenda": forms.Textarea(attrs={"class": _INPUT_CLS, "rows": 4}),
        }

    def __init__(self, *args, manager_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Stash manager_id for clean_team_member — without it, the cleaned
        # team_member lookup is unscoped and accepts any tenant's member id.
        self.manager_id = manager_id
        # Replace the ModelChoiceField with a plain ChoiceField so we can
        # add "(none)" and "All team members" sentinels.
        if manager_id is not None:
            members = TeamMember.objects.active_for_manager(manager_id).order_by("name")
            choices = [("", "(none)"), ("all", "All team members")]
            choices += [(str(m.id), m.name) for m in members]
            self.fields["team_member"] = forms.ChoiceField(
                choices=choices,
                required=False,
                widget=forms.Select(attrs={"class": _INPUT_CLS}),
            )
        if not self.initial.get("duration_minutes"):
            self.fields["duration_minutes"].initial = 30

    def clean_team_member(self):
        val = self.cleaned_data.get("team_member")
        if val == "all":
            return "all"  # sentinel — view handles expansion
        if val:
            if self.manager_id is None:
                raise forms.ValidationError("Form constructed without manager scope.")
            try:
                return TeamMember.objects.for_manager(self.manager_id).get(pk=int(val))
            except (TeamMember.DoesNotExist, ValueError):
                raise forms.ValidationError("Invalid team member.")
        return None

    def clean_scheduled_date(self):
        d = self.cleaned_data["scheduled_date"]
        return d.isoformat()  # text in DB

    # scheduled_time comes in as "HH:MM" string from the ChoiceField —
    # already in the schema's TEXT format, no conversion needed.

    def clean(self):
        cleaned = super().clean()

        # Default title from event_type if blank.
        title = (cleaned.get("title") or "").strip()
        if not title:
            et = cleaned.get("event_type") or "other"
            cleaned["title"] = dict(EVENT_TYPE_CHOICES).get(et, "Event")
        else:
            cleaned["title"] = title

        # Phase 5.2b — recurrence validation. until_date is only
        # meaningful when a rule is selected; if rule is blank we ignore
        # any until_date the user might have set. If rule is set AND
        # until_date is set, until_date must be on/after scheduled_date.
        rule = cleaned.get("recurrence_rule") or ""
        until = cleaned.get("until_date")
        sched = cleaned.get("scheduled_date")
        if rule and until and sched:
            # cleaned_data["scheduled_date"] is already an iso string
            # because clean_scheduled_date ran. Compare via fromisoformat.
            from datetime import date as _date
            sched_d = _date.fromisoformat(sched) if isinstance(sched, str) else sched
            if until < sched_d:
                self.add_error("until_date", "Until-date must be on/after the start date.")
        if not rule:
            cleaned["until_date"] = None

        return cleaned


class EventEditForm(forms.ModelForm):
    """Phase 6 (D1 resolution) — edit an existing event.

    Per the D2 contract (Outlook owns *when*, MT owns *context*),
    title / agenda / location / duration are MT's domain and edit
    cleanly. scheduled_date / scheduled_time are editable but the
    template shows an "Outlook isn't notified" warning.

    Recurrence is intentionally NOT a field here — editing one
    occurrence in a series does not propagate to siblings (CLAUDE.md).
    To extend or stop a series, use the schedule flow.
    """

    scheduled_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": _INPUT_CLS}),
    )
    scheduled_time = forms.ChoiceField(
        choices=TIME_CHOICES,
        widget=forms.Select(attrs={"class": _INPUT_CLS}),
    )
    event_type = forms.ChoiceField(
        choices=EVENT_TYPE_CHOICES,
        widget=forms.Select(attrs={"class": _INPUT_CLS}),
    )

    class Meta:
        model = Event
        fields = [
            "title", "event_type",
            "scheduled_date", "scheduled_time",
            "team_member", "duration_minutes", "location", "agenda",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": _INPUT_CLS}),
            "team_member": forms.Select(attrs={"class": _INPUT_CLS}),
            "duration_minutes": forms.NumberInput(attrs={
                "class": _INPUT_CLS, "min": 15, "step": 15,
            }),
            "location": forms.TextInput(attrs={
                "class": _INPUT_CLS,
                "placeholder": "Office / meeting link",
            }),
            "agenda": forms.Textarea(attrs={"class": _INPUT_CLS, "rows": 4}),
        }

    def __init__(self, *args, manager_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        if manager_id is not None:
            self.fields["team_member"].queryset = (
                TeamMember.objects.active_for_manager(manager_id).order_by("name")
            )
            self.fields["team_member"].required = False
            self.fields["team_member"].empty_label = "(none)"
        # Pre-populate the native date/time choices from the existing
        # values (stored as TEXT, roundtrip as strings).
        if self.instance and self.instance.pk:
            from datetime import date as _date
            try:
                self.initial["scheduled_date"] = _date.fromisoformat(
                    self.instance.scheduled_date
                )
            except (TypeError, ValueError):
                logging.getLogger(__name__).warning(
                    "Event %s has unparseable scheduled_date %r; leaving "
                    "form initial unset",
                    self.instance.pk, self.instance.scheduled_date,
                )
            self.initial["scheduled_time"] = self.instance.scheduled_time

    def clean_scheduled_date(self):
        d = self.cleaned_data["scheduled_date"]
        return d.isoformat()
    # scheduled_time is already "HH:MM" string from the ChoiceField.


# Phase 5.3 — Action items / "To Do"
# action_items.status CHECK constraint allows 'pending', 'in_progress',
# 'completed'. The form only exposes the create path (status defaults
# to 'pending' on the model); transitions happen via HTMX endpoints.



# Phase 5.3.1 — "(no time)" is the empty-string sentinel for due_time.
TIME_CHOICES_OPTIONAL = [("", "(no time)")] + TIME_CHOICES


class ActionItemForm(forms.ModelForm):
    """The to-do list is the manager's own work. assignee was removed
    per user feedback: work entrusted to a direct lives in Delegations,
    not here, so an assignee field added confusion."""

    description = forms.CharField(
        widget=forms.TextInput(attrs={
            "class": _INPUT_CLS,
            "placeholder": "What you need to do",
        }),
    )
    due_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": _INPUT_CLS}),
    )
    due_time = forms.ChoiceField(
        choices=TIME_CHOICES_OPTIONAL,
        required=False,
        widget=forms.Select(attrs={"class": _INPUT_CLS}),
    )

    class Meta:
        model = ActionItem
        fields = ["description", "due_date", "due_time", "event"]
        widgets = {
            "event": forms.Select(attrs={"class": _INPUT_CLS}),
        }

    def __init__(self, *args, manager_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Scope event dropdown to this manager's events; bare
        # ModelChoiceField would leak across tenants.
        if manager_id is not None:
            self.fields["event"].queryset = (
                Event.objects.for_manager(manager_id)
                .order_by("-scheduled_date", "-scheduled_time")
            )
            self.fields["event"].required = False
            self.fields["event"].empty_label = "(none)"

    def clean_due_date(self):
        d = self.cleaned_data.get("due_date")
        return d.isoformat() if d else None

    def clean_due_time(self):
        # ChoiceField returns the raw value; "" sentinel maps to NULL.
        t = self.cleaned_data.get("due_time")
        return t or None


# Phase 5.4 — Journal entries

from .models import JournalEntry  # noqa: E402

ENTRY_TYPE_CHOICES = [
    ("daily", "Daily"),
    ("weekly", "Weekly reflection"),
    ("reflection", "Reflection"),
]

MOOD_CHOICES = [
    ("", "—"),
    ("1", "1 — Rough"),
    ("2", "2 — Low"),
    ("3", "3 — Okay"),
    ("4", "4 — Good"),
    ("5", "5 — Great"),
]

ENERGY_CHOICES = [
    ("", "—"),
    ("1", "1 — Drained"),
    ("2", "2 — Low"),
    ("3", "3 — Steady"),
    ("4", "4 — High"),
    ("5", "5 — Fired up"),
]


class JournalEntryForm(forms.ModelForm):
    """Daily or weekly journal entry. Mood and energy are optional 1-5
    scales. Tags are freeform comma-separated text."""

    entry_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": _INPUT_CLS}),
    )
    entry_type = forms.ChoiceField(
        choices=ENTRY_TYPE_CHOICES,
        widget=forms.Select(attrs={"class": _INPUT_CLS}),
        initial="daily",
    )
    mood = forms.ChoiceField(
        choices=MOOD_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": _INPUT_CLS}),
    )
    energy = forms.ChoiceField(
        choices=ENERGY_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": _INPUT_CLS}),
    )

    class Meta:
        model = JournalEntry
        fields = [
            "entry_date", "entry_type", "content",
            "mood", "energy", "private_notes", "tags",
        ]
        widgets = {
            "content": forms.Textarea(attrs={
                "class": _INPUT_CLS,
                "rows": 5,
                "placeholder": "What's on your mind?",
            }),
            "private_notes": forms.Textarea(attrs={
                "class": _INPUT_CLS,
                "rows": 3,
                "placeholder": "What are you working on about yourself?",
            }),
            "tags": forms.TextInput(attrs={
                "class": _INPUT_CLS,
                "placeholder": "e.g. delegation, feedback, hiring",
            }),
        }

    def clean_entry_date(self):
        d = self.cleaned_data["entry_date"]
        return d.isoformat()  # text in DB

    def clean_mood(self):
        v = self.cleaned_data.get("mood")
        return int(v) if v else None

    def clean_energy(self):
        v = self.cleaned_data.get("energy")
        return int(v) if v else None


# ============================================================
# Phase 5.5 — Goals + Skills + Development Plans
# ============================================================

GOAL_STATUS_CHOICES = [
    ("not_started", "Not started"),
    ("in_progress", "In progress"),
    ("met", "Met"),
    ("exceeded", "Exceeded"),
    ("partially_met", "Partially met"),
    ("not_met", "Not met"),
]

PROFICIENCY_CHOICES = [
    ("learning", "Learning"),
    ("developing", "Developing"),
    ("proficient", "Proficient"),
    ("expert", "Expert"),
]

PLAN_STATUS_CHOICES = [
    ("active", "Active"),
    ("completed", "Completed"),
    ("paused", "Paused"),
]


def _current_quarter():
    from datetime import date
    q = (date.today().month - 1) // 3 + 1
    return f"Q{q} {date.today().year}"


class GoalForm(forms.ModelForm):
    target_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": _INPUT_CLS}),
    )
    status = forms.ChoiceField(
        choices=GOAL_STATUS_CHOICES,
        widget=forms.Select(attrs={"class": _INPUT_CLS}),
        initial="not_started",
    )

    class Meta:
        model = Goal
        fields = [
            "team_member", "quarter", "description",
            "key_results", "target_date", "status",
        ]
        widgets = {
            "team_member": forms.Select(attrs={"class": _INPUT_CLS}),
            "quarter": forms.TextInput(attrs={
                "class": _INPUT_CLS, "placeholder": "e.g. Q2 2026",
            }),
            "description": forms.TextInput(attrs={
                "class": _INPUT_CLS, "placeholder": "Goal description",
            }),
            "key_results": forms.Textarea(attrs={
                "class": _INPUT_CLS, "rows": 3,
                "placeholder": "One key result per line",
            }),
        }

    def __init__(self, *args, manager_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        if manager_id is not None:
            self.fields["team_member"].queryset = (
                TeamMember.objects.active_for_manager(manager_id).order_by("name")
            )
            self.fields["team_member"].required = True
            self.fields["team_member"].empty_label = "Select team member"
        if not self.initial.get("quarter"):
            self.fields["quarter"].initial = _current_quarter()

    def clean_target_date(self):
        d = self.cleaned_data.get("target_date")
        return d.isoformat() if d else None


class SkillForm(forms.ModelForm):
    proficiency = forms.ChoiceField(
        choices=PROFICIENCY_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": _INPUT_CLS}),
    )
    is_strength = forms.BooleanField(required=False)
    is_growth_area = forms.BooleanField(required=False)

    class Meta:
        model = Skill
        fields = [
            "team_member", "skill_name", "proficiency",
            "is_strength", "is_growth_area", "notes",
        ]
        widgets = {
            "team_member": forms.Select(attrs={"class": _INPUT_CLS}),
            "skill_name": forms.TextInput(attrs={
                "class": _INPUT_CLS, "placeholder": "e.g. Public speaking",
            }),
            "notes": forms.Textarea(attrs={
                "class": _INPUT_CLS, "rows": 2,
                "placeholder": "Optional notes",
            }),
        }

    def __init__(self, *args, manager_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        if manager_id is not None:
            self.fields["team_member"].queryset = (
                TeamMember.objects.active_for_manager(manager_id).order_by("name")
            )
            self.fields["team_member"].required = True
            self.fields["team_member"].empty_label = "Select team member"

    def clean_is_strength(self):
        return 1 if self.cleaned_data.get("is_strength") else 0

    def clean_is_growth_area(self):
        return 1 if self.cleaned_data.get("is_growth_area") else 0


class DevelopmentPlanForm(forms.ModelForm):
    target_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": _INPUT_CLS}),
    )
    status = forms.ChoiceField(
        choices=PLAN_STATUS_CHOICES,
        widget=forms.Select(attrs={"class": _INPUT_CLS}),
        initial="active",
    )

    class Meta:
        model = DevelopmentPlan
        fields = [
            "team_member", "title", "description",
            "target_date", "status",
        ]
        widgets = {
            "team_member": forms.Select(attrs={"class": _INPUT_CLS}),
            "title": forms.TextInput(attrs={
                "class": _INPUT_CLS, "placeholder": "Plan title",
            }),
            "description": forms.Textarea(attrs={
                "class": _INPUT_CLS, "rows": 3,
                "placeholder": "What this plan covers",
            }),
        }

    def __init__(self, *args, manager_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        if manager_id is not None:
            self.fields["team_member"].queryset = (
                TeamMember.objects.active_for_manager(manager_id).order_by("name")
            )
            self.fields["team_member"].required = True
            self.fields["team_member"].empty_label = "Select team member"

    def clean_target_date(self):
        d = self.cleaned_data.get("target_date")
        return d.isoformat() if d else None


class MilestoneForm(forms.ModelForm):
    target_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": _INPUT_CLS}),
    )

    class Meta:
        model = Milestone
        fields = ["description", "target_date"]
        widgets = {
            "description": forms.TextInput(attrs={
                "class": _INPUT_CLS, "placeholder": "Milestone description",
            }),
        }

    def clean_target_date(self):
        d = self.cleaned_data.get("target_date")
        return d.isoformat() if d else None


class CareerConversationForm(forms.ModelForm):
    conversation_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": _INPUT_CLS}),
    )

    class Meta:
        model = CareerConversation
        fields = [
            "team_member", "conversation_date", "topic",
            "notes", "next_steps",
        ]
        widgets = {
            "team_member": forms.Select(attrs={"class": _INPUT_CLS}),
            "topic": forms.TextInput(attrs={
                "class": _INPUT_CLS, "placeholder": "Discussion topic",
            }),
            "notes": forms.Textarea(attrs={
                "class": _INPUT_CLS, "rows": 3,
                "placeholder": "Key points from the conversation",
            }),
            "next_steps": forms.Textarea(attrs={
                "class": _INPUT_CLS, "rows": 2,
                "placeholder": "Agreed next steps",
            }),
        }

    def __init__(self, *args, manager_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        if manager_id is not None:
            self.fields["team_member"].queryset = (
                TeamMember.objects.active_for_manager(manager_id).order_by("name")
            )
            self.fields["team_member"].required = True
            self.fields["team_member"].empty_label = "Select team member"

    def clean_conversation_date(self):
        d = self.cleaned_data["conversation_date"]
        return d.isoformat()


# ============================================================
# Phase 5.6 — Delegations + Decisions + Running Notes
# ============================================================

AUTONOMY_CHOICES = [
    ("guided", "Guided"),
    ("autonomous", "Autonomous"),
    ("delegated", "Fully delegated"),
]

DELEGATION_STATUS_CHOICES = [
    ("active", "Active"),
    ("completed", "Completed"),
    ("stalled", "Stalled"),
]

DECISION_STATUS_CHOICES = [
    ("active", "Active"),
    ("validated", "Validated"),
    ("revised", "Revised"),
    ("reversed", "Reversed"),
]

NOTE_CATEGORY_CHOICES = [
    ("general", "General"),
    ("meeting_prep", "Meeting prep"),
    ("observation", "Observation"),
    ("follow_up", "Follow-up"),
    # "praise" intentionally absent — praise is structured feedback,
    # captured via the Feedback model with feedback_type="positive"
    # (SBI: situation, behavior, impact). Migration 0010 converted any
    # existing praise RunningNotes to Feedback rows; this guard keeps
    # users from re-creating the duplicate channel.
]


class DelegationForm(forms.ModelForm):
    check_in_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": _INPUT_CLS}),
    )
    autonomy_level = forms.ChoiceField(
        choices=AUTONOMY_CHOICES,
        widget=forms.Select(attrs={"class": _INPUT_CLS}),
        initial="guided",
    )
    status = forms.ChoiceField(
        choices=DELEGATION_STATUS_CHOICES,
        widget=forms.Select(attrs={"class": _INPUT_CLS}),
        initial="active",
    )

    class Meta:
        model = Delegation
        fields = [
            "team_member", "task", "outcome_expected",
            "autonomy_level", "check_in_date", "notes", "status",
        ]
        widgets = {
            "team_member": forms.Select(attrs={"class": _INPUT_CLS}),
            "task": forms.TextInput(attrs={
                "class": _INPUT_CLS, "placeholder": "What you're delegating",
            }),
            "outcome_expected": forms.Textarea(attrs={
                "class": _INPUT_CLS, "rows": 2,
                "placeholder": "What does done look like?",
            }),
            "notes": forms.Textarea(attrs={
                "class": _INPUT_CLS, "rows": 2,
                "placeholder": "Additional context",
            }),
        }

    def __init__(self, *args, manager_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        if manager_id is not None:
            self.fields["team_member"].queryset = (
                TeamMember.objects.active_for_manager(manager_id).order_by("name")
            )
            self.fields["team_member"].required = False
            self.fields["team_member"].empty_label = "(none)"

    def clean_check_in_date(self):
        d = self.cleaned_data.get("check_in_date")
        return d.isoformat() if d else None


class DecisionForm(forms.ModelForm):
    review_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": _INPUT_CLS}),
    )
    status = forms.ChoiceField(
        choices=DECISION_STATUS_CHOICES,
        widget=forms.Select(attrs={"class": _INPUT_CLS}),
        initial="active",
    )

    class Meta:
        model = Decision
        fields = [
            "title", "context", "alternatives", "rationale",
            "expected_outcome", "review_date", "status", "actual_outcome",
        ]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": _INPUT_CLS, "placeholder": "Decision title",
            }),
            "context": forms.Textarea(attrs={
                "class": _INPUT_CLS, "rows": 2,
                "placeholder": "What prompted this decision?",
            }),
            "alternatives": forms.Textarea(attrs={
                "class": _INPUT_CLS, "rows": 2,
                "placeholder": "What else was considered?",
            }),
            "rationale": forms.Textarea(attrs={
                "class": _INPUT_CLS, "rows": 2,
                "placeholder": "Why this choice?",
            }),
            "expected_outcome": forms.TextInput(attrs={
                "class": _INPUT_CLS, "placeholder": "What should happen if this works",
            }),
            "actual_outcome": forms.TextInput(attrs={
                "class": _INPUT_CLS, "placeholder": "What actually happened (fill on review)",
            }),
        }

    def clean_review_date(self):
        d = self.cleaned_data.get("review_date")
        return d.isoformat() if d else None


class RunningNoteForm(forms.ModelForm):
    note_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": _INPUT_CLS}),
    )
    category = forms.ChoiceField(
        choices=NOTE_CATEGORY_CHOICES,
        widget=forms.Select(attrs={"class": _INPUT_CLS}),
        initial="general",
    )

    class Meta:
        model = RunningNote
        fields = ["team_member", "note_date", "content", "category"]
        widgets = {
            "team_member": forms.Select(attrs={"class": _INPUT_CLS}),
            "content": forms.Textarea(attrs={
                "class": _INPUT_CLS, "rows": 3,
                "placeholder": "What happened or what you observed",
            }),
        }

    def __init__(self, *args, manager_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        if manager_id is not None:
            self.fields["team_member"].queryset = (
                TeamMember.objects.active_for_manager(manager_id).order_by("name")
            )
            self.fields["team_member"].required = False
            self.fields["team_member"].empty_label = "All members (broadcast)"
        if not self.initial.get("note_date"):
            from datetime import date
            self.fields["note_date"].initial = date.today()

    def clean_note_date(self):
        d = self.cleaned_data["note_date"]
        return d.isoformat()


# Phase 5.6b — Feedback

FEEDBACK_TYPE_CHOICES = [
    ("positive", "Positive"),
    ("constructive", "Constructive"),
]


class FeedbackForm(forms.ModelForm):
    feedback_type = forms.ChoiceField(
        choices=FEEDBACK_TYPE_CHOICES,
        widget=forms.Select(attrs={"class": _INPUT_CLS}),
    )

    class Meta:
        model = Feedback
        fields = [
            "team_member", "feedback_type",
            "situation", "behavior", "impact",
        ]
        widgets = {
            "team_member": forms.Select(attrs={"class": _INPUT_CLS}),
            "situation": forms.Textarea(attrs={
                "class": _INPUT_CLS, "rows": 2,
                "placeholder": "When/where did this happen?",
            }),
            "behavior": forms.Textarea(attrs={
                "class": _INPUT_CLS, "rows": 2,
                "placeholder": "What specifically did they do?",
            }),
            "impact": forms.Textarea(attrs={
                "class": _INPUT_CLS, "rows": 2,
                "placeholder": "What was the result or effect?",
            }),
        }

    def __init__(self, *args, manager_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        if manager_id is not None:
            self.fields["team_member"].queryset = (
                TeamMember.objects.active_for_manager(manager_id).order_by("name")
            )
            self.fields["team_member"].required = True
            self.fields["team_member"].empty_label = "Select team member"


# Phase 5.7 — Settings

COMMON_TIMEZONES = [
    ("America/New_York", "Eastern (New York)"),
    ("America/Chicago", "Central (Chicago)"),
    ("America/Denver", "Mountain (Denver)"),
    ("America/Los_Angeles", "Pacific (Los Angeles)"),
    ("America/Phoenix", "Arizona (no DST)"),
    ("America/Anchorage", "Alaska"),
    ("Pacific/Honolulu", "Hawaii"),
    ("UTC", "UTC"),
]


class ManagerSettingsForm(forms.ModelForm):
    timezone = forms.ChoiceField(
        choices=COMMON_TIMEZONES,
        required=False,
        widget=forms.Select(attrs={"class": _INPUT_CLS}),
    )

    class Meta:
        model = Manager
        fields = ["display_name", "timezone"]
        widgets = {
            "display_name": forms.TextInput(attrs={
                "class": _INPUT_CLS, "placeholder": "Your name",
            }),
        }


class ConfigSettingsForm(forms.Form):
    """Non-model settings stored in the Config table (one row per
    key, per manager). Sensitive fields use PasswordInput and an
    empty submission means 'keep existing value'."""

    anthropic_api_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            "class": _INPUT_CLS,
            "placeholder": "sk-ant-... (leave blank to keep existing key)",
            "autocomplete": "new-password",
        }),
    )
    manager_name = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": _INPUT_CLS, "placeholder": "Your name (used in email From:)",
        }),
    )
    manager_email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={
            "class": _INPUT_CLS, "placeholder": "you@example.com",
        }),
    )
    smtp_server = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": _INPUT_CLS, "placeholder": "smtp.gmail.com",
        }),
    )
    smtp_port = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": _INPUT_CLS, "placeholder": "587",
        }),
    )
    smtp_user = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": _INPUT_CLS, "placeholder": "you@gmail.com",
        }),
    )
    smtp_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            "class": _INPUT_CLS,
            "placeholder": "App password (leave blank to keep existing)",
            "autocomplete": "new-password",
        }),
    )

    def clean_smtp_port(self):
        port = self.cleaned_data.get("smtp_port", "").strip()
        if port and not port.isdigit():
            raise forms.ValidationError("Port must be numeric.")
        return port


# ── One-on-One Meetings ──────────────────────────────────────────────


class OneOnOneSessionForm(forms.ModelForm):
    session_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": _INPUT_CLS}),
    )

    class Meta:
        model = OneOnOneSession
        fields = [
            "team_member", "session_date", "event",
            "direct_notes", "manager_notes", "followup_notes",
        ]
        labels = {
            "direct_notes": "Their Agenda",
            "manager_notes": "Your Agenda",
            "followup_notes": "Coaching / Their Future",
        }
        widgets = {
            "team_member": forms.Select(attrs={"class": _INPUT_CLS}),
            "event": forms.Select(attrs={"class": _INPUT_CLS}),
            "direct_notes": forms.Textarea(attrs={
                "class": _INPUT_CLS, "rows": 6,
                "placeholder": "Their 10 minutes — what's on their mind? Start here.",
            }),
            "manager_notes": forms.Textarea(attrs={
                "class": _INPUT_CLS, "rows": 6,
                "placeholder": "Your 10 minutes — what you need to cover.",
            }),
            "followup_notes": forms.Textarea(attrs={
                "class": _INPUT_CLS, "rows": 6,
                "placeholder": "Development, coaching, career growth.",
            }),
        }

    def __init__(self, *args, manager_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        if manager_id is not None:
            self.fields["team_member"].queryset = (
                TeamMember.objects.active_for_manager(manager_id)
                .order_by("name")
            )
            self.fields["event"].queryset = (
                Event.objects.for_manager(manager_id)
                .filter(event_type="one_on_one")
                .order_by("-scheduled_date")
            )
        self.fields["team_member"].empty_label = "Select team member"
        self.fields["event"].required = False
        self.fields["event"].empty_label = "(no linked event)"
        self.fields["direct_notes"].required = False
        self.fields["manager_notes"].required = False
        self.fields["followup_notes"].required = False

    def clean_session_date(self):
        d = self.cleaned_data["session_date"]
        return d.isoformat()


class MeetingActionItemForm(forms.Form):
    description = forms.CharField(
        widget=forms.TextInput(attrs={
            "class": _INPUT_CLS,
            "placeholder": "Action item description",
        }),
    )
    due_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": _INPUT_CLS}),
    )

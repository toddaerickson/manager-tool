"""Forms for the core app.

manager_id is never a form field — it's set by the view from
request.manager. start_date / scheduled_date / scheduled_time stay as
TEXT in the underlying schema (Streamlit convention, CLAUDE.md);
forms convert from native date/time input to ISO/HH:MM strings on save.
"""

from django import forms

from .models import Event, TeamMember


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
                "class": _INPUT_CLS, "min": 5, "step": 5,
            }),
            "location": forms.TextInput(attrs={
                "class": _INPUT_CLS,
                "placeholder": "Office / meeting link",
            }),
            "agenda": forms.Textarea(attrs={"class": _INPUT_CLS, "rows": 4}),
        }

    def __init__(self, *args, manager_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Scope team_member dropdown to this manager's roster — bare
        # ModelChoiceField would show all rows across tenants.
        if manager_id is not None:
            self.fields["team_member"].queryset = (
                TeamMember.objects.active_for_manager(manager_id).order_by("name")
            )
            self.fields["team_member"].required = False
            self.fields["team_member"].empty_label = "(none)"
        if not self.initial.get("duration_minutes"):
            self.fields["duration_minutes"].initial = 30

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

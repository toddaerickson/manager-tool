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


class EventForm(forms.ModelForm):
    """Schedule a one-off event. Recurring events come in Phase 5.2b
    (separate code path: db.create_recurring_events / _materialize_in_txn)."""

    scheduled_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": _INPUT_CLS}),
    )
    scheduled_time = forms.TimeField(
        widget=forms.TimeInput(attrs={"type": "time", "class": _INPUT_CLS}),
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

    def clean_scheduled_time(self):
        t = self.cleaned_data["scheduled_time"]
        return t.strftime("%H:%M")  # text in DB

    def clean(self):
        # Default title from event_type if blank — done at form level so
        # both title and event_type are populated in cleaned_data.
        # (Per-field clean methods run in declaration order; can't rely
        # on event_type being available in clean_title.)
        cleaned = super().clean()
        title = (cleaned.get("title") or "").strip()
        if not title:
            et = cleaned.get("event_type") or "other"
            cleaned["title"] = dict(EVENT_TYPE_CHOICES).get(et, "Event")
        else:
            cleaned["title"] = title
        return cleaned

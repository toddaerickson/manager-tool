from django import forms
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from .models import TeamMember


class TeamMemberForm(forms.ModelForm):
    """Add-member form. manager_id is set in the view from request.manager;
    not user-editable. start_date stays TextField on the model (Streamlit
    convention, CLAUDE.md), so we use a date input + format on save."""

    start_date = forms.DateField(required=False)

    class Meta:
        model = TeamMember
        fields = ["name", "email", "role", "start_date", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Full name"}),
            "email": forms.EmailInput(attrs={"placeholder": "name@company.com"}),
            "role": forms.TextInput(attrs={"placeholder": "Engineer / PM / ..."}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def clean_start_date(self):
        d = self.cleaned_data.get("start_date")
        return d.isoformat() if d else None


def hello(request):
    """Public landing page. Shows a sign-in link if anonymous, otherwise
    a link to the dashboard."""
    if request.user.is_authenticated:
        body = (
            f"Hello {request.user.email or 'authenticated user'} — "
            f"go to /dashboard/ to see your team.\n"
        )
    else:
        body = (
            "Manager Tool — Django scaffold.\n"
            "Sign in: /accounts/google/login/\n"
        )
    return HttpResponse(body, content_type="text/plain")


def sentry_debug(request):
    """Trigger a deliberate exception so Sentry can prove it captures errors.

    Phase 1 → 2 gate: a hit on this URL must show up in the Sentry dashboard
    within 60 seconds.
    """
    raise ZeroDivisionError("sentry-debug: deliberate test exception")


@login_required
def dashboard(request):
    """Phase 3 → 4: template-rendered dashboard with a Tailwind sidebar
    layout. The overview panel loads via HTMX (see dashboard.html)."""
    if request.manager is None:
        return HttpResponseForbidden(
            f"No manager profile is linked to {request.user.email}. "
            "Ask an administrator to create one."
        )
    return render(request, "dashboard.html")


def _require_manager(request):
    """Returns (manager, None) if OK; (None, response) to short-circuit."""
    if request.manager is None:
        return None, HttpResponseForbidden(
            f"No manager profile is linked to {request.user.email}."
        )
    return request.manager, None


@login_required
def team_members_list(request):
    """Phase 5.1 — Team Members list + Add form. HTMX target is the
    member-list partial; the form posts to /team/add/."""
    manager, err = _require_manager(request)
    if err:
        return err
    members = TeamMember.objects.for_manager(manager.id).order_by("name")
    return render(request, "team_members.html", {
        "members": members,
        "form": TeamMemberForm(),
    })


@login_required
@require_http_methods(["POST"])
def team_members_add(request):
    """HTMX endpoint: validate + create + return updated list partial.
    On error, returns the form fragment with errors so HTMX swaps it back
    in. Both responses target #member-list (form re-render handles itself)."""
    manager, err = _require_manager(request)
    if err:
        return err
    form = TeamMemberForm(request.POST)
    if not form.is_valid():
        return render(
            request, "_partials/team_member_form.html",
            {"form": form}, status=422,
        )
    member = form.save(commit=False)
    member.manager_id = manager.id
    member.save()
    members = TeamMember.objects.for_manager(manager.id).order_by("name")
    # Return BOTH the cleared form (oob swap) and the updated list.
    return render(request, "_partials/team_member_list_after_add.html", {
        "members": members,
        "form": TeamMemberForm(),
    })


@login_required
def dashboard_overview(request):
    """HTMX partial — returns the overview panel HTML fragment.

    Mirrors the Streamlit `_dashboard_bundle` pattern (one cached call per
    manager_id) but with lazy loading so the page shell renders before
    any DB work happens.
    """
    if request.manager is None:
        return HttpResponseForbidden("No manager profile.")
    ctx = {
        "team_member_count": TeamMember.objects.for_manager(request.manager.id).count(),
    }
    return render(request, "_partials/dashboard_overview.html", ctx)

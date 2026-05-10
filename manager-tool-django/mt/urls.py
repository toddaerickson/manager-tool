from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from core import views as core_views

urlpatterns = [
    path("", core_views.hello, name="hello"),
    path("dashboard/", core_views.dashboard, name="dashboard"),
    path("dashboard/panels/overview/", core_views.dashboard_overview, name="dashboard-overview"),
    path("team/", core_views.team_members_list, name="team"),
    path("team/add/", core_views.team_members_add, name="team-add"),
    path("team/<int:member_id>/delete/", core_views.team_members_delete, name="team-delete"),
    path("team/<int:member_id>/restore/", core_views.team_members_restore, name="team-restore"),
    path("events/", core_views.events_upcoming, name="events-upcoming"),
    path("events/schedule/", core_views.events_schedule, name="events-schedule"),
    path("events/<int:event_id>/", core_views.events_detail, name="events-detail"),
    path("events/<int:event_id>/edit/", core_views.events_edit, name="events-edit"),
    path("events/<int:event_id>/complete/", core_views.events_complete, name="events-complete"),
    path("events/<int:event_id>/delete/", core_views.events_delete, name="events-delete"),
    path("todos/", core_views.todos_list, name="todos"),
    path("todos/add/", core_views.todos_add, name="todos-add"),
    path("todos/<int:todo_id>/complete/", core_views.todos_complete, name="todos-complete"),
    path("todos/<int:todo_id>/uncomplete/", core_views.todos_uncomplete, name="todos-uncomplete"),
    path("todos/<int:todo_id>/delete/", core_views.todos_delete, name="todos-delete"),
    path("journal/", core_views.journal_list, name="journal"),
    path("journal/add/", core_views.journal_add, name="journal-add"),
    path("journal/<int:entry_id>/edit/", core_views.journal_edit, name="journal-edit"),
    path("sentry-debug/", core_views.sentry_debug, name="sentry-debug"),
    # Force Google-only by short-circuiting allauth's default form. The
    # email/password page exists in allauth but we never want users to
    # hit it. Must be registered BEFORE the include() so it wins.
    path(
        "accounts/login/",
        RedirectView.as_view(url="/accounts/google/login/", permanent=False),
    ),
    path("accounts/", include("allauth.urls")),
    path("admin/", admin.site.urls),
]

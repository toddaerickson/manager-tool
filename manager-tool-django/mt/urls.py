from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from core import views as core_views

urlpatterns = [
    path("", core_views.hello, name="hello"),
    path("dashboard/", core_views.dashboard, name="dashboard"),
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

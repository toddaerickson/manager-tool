from django.contrib import admin
from django.urls import include, path

from core import views as core_views

urlpatterns = [
    path("", core_views.hello, name="hello"),
    path("dashboard/", core_views.dashboard, name="dashboard"),
    path("sentry-debug/", core_views.sentry_debug, name="sentry-debug"),
    path("accounts/", include("allauth.urls")),
    path("admin/", admin.site.urls),
]

"""Allauth account adapter that disables open signup.

The Django app is Google-OAuth-only. The `mt/urls.py` redirect for
`/accounts/login/` covers the login page, but `include("allauth.urls")`
still mounts `/accounts/signup/` and related views. With
`ACCOUNT_EMAIL_VERIFICATION = "none"` and the bridge middleware mapping
`email__iexact` to existing Manager rows, an open signup form is an
account-takeover surface: register with a known manager's email and
inherit their tenant data.

This adapter shuts that down at the framework level. The defensive
`/accounts/signup/` redirect in urls.py belts-and-suspenders the URL
path, but the framework hook here is the canonical mechanism.
"""

from allauth.account.adapter import DefaultAccountAdapter


class ClosedSignupAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        return False

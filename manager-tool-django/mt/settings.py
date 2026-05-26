"""Django settings for the manager-tool migration target.

Loaded values come from a .env file at the project root (see .env.template
in the parent repo). Production sets MANAGER_TOOL_ENV=prod which gates
DEBUG off and enforces the security headers documented in
.streamlit/config.toml's AUDIT L12 comment block.
"""

import os
from pathlib import Path

import environ
import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration


BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    MANAGER_TOOL_ENV=(str, "dev"),
    SENTRY_DSN=(str, ""),
)
environ.Env.read_env(BASE_DIR / ".env")

IS_PROD = env("MANAGER_TOOL_ENV") == "prod"
DEBUG = not IS_PROD

# Fail-closed: in prod, refuse to start if the per-tenant config encryption
# key is missing. The Config table stores SMTP creds and Anthropic API
# keys Fernet-encrypted by core/services/encryption.py — a missing key
# would either silently store new values plaintext or 500 on first read
# of an encrypted row. Either is unacceptable on a multi-tenant deploy.
# Mirrors Streamlit's L12 / M9 audit guarantee that the cutover migrated
# us off Streamlit was supposed to preserve. Dev is exempt — encryption
# auto-generates a per-checkout keyfile in non-prod (see encryption.py).
if IS_PROD and not os.environ.get("CONFIG_ENCRYPTION_KEY"):
    raise RuntimeError(
        "CONFIG_ENCRYPTION_KEY is required when MANAGER_TOOL_ENV=prod"
    )

SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

# Render injects RENDER_EXTERNAL_HOSTNAME at runtime. Add it to
# ALLOWED_HOSTS and CSRF_TRUSTED_ORIGINS automatically so the deploy
# works without the user having to know the hostname up front.
_render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if _render_host and _render_host not in ALLOWED_HOSTS:
    ALLOWED_HOSTS = list(ALLOWED_HOSTS) + [_render_host]

CSRF_TRUSTED_ORIGINS = []
if _render_host:
    CSRF_TRUSTED_ORIGINS.append(f"https://{_render_host}")


# --- Apps + middleware --------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "django_htmx",
    "tailwind",
    "core",
    "coaching",
]

SITE_ID = 1

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "core.middleware.ManagerBridgeMiddleware",
]

ROOT_URLCONF = "mt.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "mt.wsgi.application"


# --- Database -----------------------------------------------------------

DATABASES = {"default": env.db("DATABASE_URL")}


# --- Auth ---------------------------------------------------------------

AUTHENTICATION_BACKENDS = [
    # ModelBackend removed — Google OAuth only via allauth.
    "allauth.account.auth_backends.AuthenticationBackend",
]

# allauth >= 0.61 settings
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*"]
ACCOUNT_EMAIL_VERIFICATION = "none"
# Lock signup at the framework level — the app is Google-OAuth-only and the
# email-iexact bridge middleware makes any open signup an account-takeover
# surface. See core/auth_adapter.py.
ACCOUNT_ADAPTER = "core.auth_adapter.ClosedSignupAdapter"
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": env("GOOGLE_OAUTH_CLIENT_ID", default=""),
            "secret": env("GOOGLE_OAUTH_CLIENT_SECRET", default=""),
            "key": "",
        },
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
    },
}
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/"
# Google-only auth: @login_required redirects straight to the Google
# OAuth flow rather than allauth's default email/password form. Set
# SOCIALACCOUNT_LOGIN_ON_GET so Google flow starts on GET (no
# intermediate "click to continue" page).
LOGIN_URL = "/accounts/google/login/"
SOCIALACCOUNT_LOGIN_ON_GET = True


# --- Internationalization ----------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/New_York"
USE_I18N = True
USE_TZ = True


# --- Static files (WhiteNoise) -----------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}


# --- Security headers (AUDIT L12 parity from .streamlit/config.toml) ---

# Always-on, harmless in dev:
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

# Production-only — break local dev if forced on:
SECURE_HSTS_SECONDS = 63072000 if IS_PROD else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = IS_PROD
SECURE_HSTS_PRELOAD = IS_PROD
SECURE_SSL_REDIRECT = IS_PROD
SESSION_COOKIE_SECURE = IS_PROD
CSRF_COOKIE_SECURE = IS_PROD
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if IS_PROD else None


# --- Observability (Sentry — wired in Phase 1 per gate) -----------------

SENTRY_DSN = env("SENTRY_DSN")
if SENTRY_DSN:
    # Local import: importing core.* at module top forces the app to
    # load before Django finishes settings parsing.
    from core.utils import sentry_before_send

    # A malformed DSN (typo, partial paste, placeholder like
    # "PASTE_VALUE_HERE") raises BadDsn from sentry_sdk. Without this
    # guard the exception bubbles out of settings.py, Django can't
    # import, and every gunicorn worker / cron run exits 1. Caught the
    # purge-deleted-members cron with this on 2026-05-25 23:48 UTC
    # during the post-PR-#107 env-var paste pass. We'd rather ship
    # without observability than have the whole service refuse to
    # start — log a warning instead.
    import logging

    try:
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[DjangoIntegration()],
            traces_sample_rate=0.1,
            send_default_pii=False,
            environment=env("MANAGER_TOOL_ENV"),
            before_send=sentry_before_send,
        )
    except Exception as e:
        logging.getLogger(__name__).warning(
            "Sentry init failed (%s); continuing without Sentry", e,
        )


DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

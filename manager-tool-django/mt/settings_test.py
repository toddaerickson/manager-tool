"""Test settings — imports prod settings, overrides DB to SQLite in-memory.

Why SQLite for unit tests: speed + zero external deps. PG-specific
behavior (composite-uniques, partial indexes, datetime/date shape) is
covered by scripts/smoke_pg_django.py which runs against a real
postgres:16 container in CI. Per CLAUDE.md, the SQLite suite alone is
NOT proof of PG safety — that's the smoke job's job.
"""

import os

# Stub the env vars settings.py requires before import
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-not-secret")
os.environ.setdefault("MANAGER_TOOL_ENV", "dev")
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
# Fixed, non-production Fernet key so the encryption helper round-trips
# in unit tests. Real prod uses a value injected via Render env vars.
os.environ.setdefault(
    "CONFIG_ENCRYPTION_KEY",
    "Wn1B-jL_-1uDuv6V4iH5b4n_NunIp1Vt7lkM2Yp9JEM=",
)
os.environ.setdefault("SENTRY_DSN", "")  # disable Sentry in tests

from .settings import *  # noqa: E402, F401, F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Speed up password hashing in tests
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Disable the journal coaching daemon thread in tests. It opens its own
# connection to the shared :memory: DB and commits a CoachingResponse
# audit row outside the test transaction, leaking rows past rollback into
# unrelated tests. Coaching generation itself is covered by coaching/tests.py.
COACHING_ENABLED = False

# Plain static storage in tests: WhiteNoise's manifest storage raises
# "Missing staticfiles manifest entry" for {% static 'css/tw.css' %}
# unless collectstatic ran first, and pytest (locally and in CI) never
# runs collectstatic. Hashing/compression is deploy behavior, not app
# logic under test.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

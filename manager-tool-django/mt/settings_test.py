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
os.environ.setdefault("CONFIG_ENCRYPTION_KEY", "")
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

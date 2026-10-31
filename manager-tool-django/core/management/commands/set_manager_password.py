"""Set or reset the login password for a manager account.

Sign-in is email + password via django-allauth. Users created by the old
Google-only flow have an unusable password, so the first step after this
change is to set one on the *existing* Django User row. Never create a
second User for the same email — ManagerBridgeMiddleware maps
request.user.email to a Manager row, and duplicate User rows make that
mapping ambiguous.

Usage:
    python manage.py set_manager_password you@example.com
    python manage.py set_manager_password you@example.com --password '...'
    python manage.py set_manager_password you@example.com --create

With no --password, a strong random password is generated and printed
once. The plaintext is never stored or logged.
"""

import secrets
import string

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from core.models import Manager

User = get_user_model()

_SYMBOLS = "!@#$%^&*()-_=+"
_ALPHABET = string.ascii_letters + string.digits + _SYMBOLS


def _generate_password(length=20):
    """Return a random password that satisfies the configured validators."""
    while True:
        pw = "".join(secrets.choice(_ALPHABET) for _ in range(length))
        if (
            any(c.islower() for c in pw)
            and any(c.isupper() for c in pw)
            and any(c.isdigit() for c in pw)
            and any(c in _SYMBOLS for c in pw)
        ):
            return pw


class Command(BaseCommand):
    help = "Set/reset the password for an existing manager login (email)."

    def add_arguments(self, parser):
        parser.add_argument(
            "email",
            help="Login email address (case-insensitive).",
        )
        parser.add_argument(
            "--password",
            default=None,
            help="Password to set. Omit to generate a strong random one.",
        )
        parser.add_argument(
            "--create",
            action="store_true",
            help="Create the auth user if none exists for this email.",
        )

    def handle(self, *args, **opts):
        email = opts["email"].strip()
        if not email:
            raise CommandError("Email is required.")

        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            if not opts["create"]:
                raise CommandError(
                    f"No auth user found for {email!r}. Pass --create to "
                    "create one (only if no Manager row exists yet)."
                )
            # Refuse to collide with an existing username: the default User
            # model requires unique usernames, and creating a second row for
            # the same person would make the email bridge ambiguous.
            clash = User.objects.filter(username__iexact=email).first()
            if clash is not None:
                raise CommandError(
                    f"A user with username {email!r} already exists (email="
                    f"{clash.email!r}). Refusing to create a duplicate."
                )
            user = User(username=email, email=email)

        generated = opts["password"] is None
        password = opts["password"] or _generate_password()
        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            raise CommandError("Password rejected: " + " ".join(exc.messages))

        user.set_password(password)
        if not user.username:
            user.username = email
        user.save()

        # allauth resolves logins by lowercased EmailAddress first, then by
        # an exact match on User.email. Ensure a row exists so sign-in is
        # case-insensitive even when User.email kept the original casing.
        ea = EmailAddress.objects.filter(user=user, email__iexact=email).first()
        if ea is None:
            EmailAddress.objects.create(
                user=user,
                email=email.lower(),
                verified=False,
                primary=not EmailAddress.objects.filter(
                    user=user, primary=True,
                ).exists(),
            )

        if not Manager.objects.filter(email__iexact=email).exists():
            self.stdout.write(self.style.WARNING(
                f"No Manager row matches {email!r}; login will succeed but "
                "the app returns 403 until a manager profile is linked."
            ))

        self.stdout.write(self.style.SUCCESS(f"Password updated for {email}."))
        if generated:
            self.stdout.write("Generated password (copy it now; it is not stored):")
            self.stdout.write(password)

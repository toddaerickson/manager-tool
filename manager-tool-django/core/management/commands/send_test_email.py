"""Send one test email through the configured `EMAIL_BACKEND`.

Operator tool for verifying `EMAIL_API_KEY` + `DEFAULT_FROM_EMAIL` without
triggering a password reset. Because account mail is delivered through
Resend's API, the recipient must be a manager address or be listed in
`EMAIL_API_ALLOWED_RECIPIENTS` — the backend drops everything else on purpose.

Usage:
    python manage.py send_test_email you@example.com
    python manage.py send_test_email you@example.com --allow-listed

Exits 1 when nothing was delivered, so it can gate a deploy check.
"""

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError

from core.email_backend import is_allowlisted, is_usable_from, mask_address
from core.models import Manager


class Command(BaseCommand):
    help = "Send one test email through the configured email backend."

    def add_arguments(self, parser):
        parser.add_argument("email", help="Recipient address.")
        parser.add_argument(
            "--allow-listed",
            action="store_true",
            help=(
                "Only assert the address is in EMAIL_API_ALLOWED_RECIPIENTS "
                "instead of requiring a Manager row."
            ),
        )

    def warn(self, message):
        self.stderr.write(self.style.WARNING(message))

    def handle(self, *args, **opts):
        email = opts["email"].strip()
        if not email:
            raise CommandError("Email is required.")

        key = getattr(settings, "EMAIL_API_KEY", "") or ""
        if not key:
            raise CommandError(
                "EMAIL_API_KEY is not set; nothing can be sent. Set it in the "
                "environment (and DEFAULT_FROM_EMAIL to an address on a domain "
                "verified in Resend)."
            )

        backend = getattr(settings, "EMAIL_BACKEND", "")
        if backend != "core.email_backend.ResendEmailBackend":
            self.warn(
                f"EMAIL_BACKEND is {backend or '(unset)'!r}, so this run does "
                "not exercise the Resend API; a success line below proves "
                "nothing about production."
            )
        if not key.startswith("re_"):
            self.warn(
                "EMAIL_API_KEY does not start with `re_`, so it probably is not "
                "a Resend key; the provider will answer HTTP 401 "
                "`validation_error: API key is invalid`."
            )
        # The backend refuses to POST at all in these two cases (it cannot tell
        # a config mistake from a provider rejection once the request is out),
        # so repeating that check here is what turns "nothing was delivered,
        # check the log" into the actual cause.
        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "") or ""
        if "resend.dev" in from_email.lower():
            # The onboarding domain can only mail the address that owns the
            # Resend account (403: "You can only send testing emails to your own
            # email address"), which is fine for a first smoke test and wrong
            # for managers.
            self.warn(
                "DEFAULT_FROM_EMAIL uses resend.dev: Resend only delivers that "
                "to the account owner's own address. Verify a domain and switch "
                "DEFAULT_FROM_EMAIL before mailing real managers."
            )
        elif not is_usable_from(from_email):
            raise CommandError(
                f"DEFAULT_FROM_EMAIL ({from_email or '(unset)'}) is not a usable "
                "address: the backend would drop the message without calling "
                "Resend. Set it to an address on a domain verified in Resend, "
                'e.g. "Manager Tool <no-reply@yourdomain.com>".'
            )

        if opts["allow_listed"]:
            if not is_allowlisted(email):
                raise CommandError(
                    f"{email} is not in EMAIL_API_ALLOWED_RECIPIENTS, so the "
                    "backend would still drop the message. Add it there "
                    "(comma-separated, exact address) or use a manager address."
                )
        elif not Manager.objects.filter(email__iexact=email).exists():
            raise CommandError(
                f"No Manager row matches {email!r}, so the backend would drop "
                "the message. Use a manager address, or add this one to "
                "EMAIL_API_ALLOWED_RECIPIENTS in the environment."
            )

        sent = send_mail(
            subject="Manager Tool test email",
            message=(
                "This is a test message from Manager Tool. If you can read it, "
                "password-reset mail will be delivered too."
            ),
            from_email=None,  # -> DEFAULT_FROM_EMAIL
            recipient_list=[email],
            fail_silently=False,
        )
        if not sent:
            raise CommandError(
                f"Nothing was delivered to {mask_address(email)} — check the "
                "`core.email_backend` ERROR log for the provider's response."
            )
        self.stdout.write(self.style.SUCCESS(
            f"Test email accepted by the provider for {mask_address(email)}."
        ))

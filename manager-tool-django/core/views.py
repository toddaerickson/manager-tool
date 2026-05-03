from django.http import HttpResponse


def hello(request):
    return HttpResponse(
        "Manager Tool — Django scaffold (Phase 1).\n"
        "Hit /sentry-debug/ to fire a test exception.\n",
        content_type="text/plain",
    )


def sentry_debug(request):
    """Trigger a deliberate exception so Sentry can prove it captures errors.

    Phase 1 → 2 gate: a hit on this URL must show up in the Sentry dashboard
    within 60 seconds.
    """
    raise ZeroDivisionError("sentry-debug: deliberate test exception")

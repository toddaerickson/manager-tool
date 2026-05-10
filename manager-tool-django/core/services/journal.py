"""Journal utilities — shared across views, digest, and coaching."""

from datetime import date, timedelta

from core.models import JournalEntry


def journal_streak(manager_id, today_iso=None):
    """Count consecutive days with a journal entry ending on today.

    Returns 0 if no entry on the anchor date.
    """
    if today_iso is None:
        today_iso = date.today().isoformat()
    dates = set(
        JournalEntry.objects.for_manager(manager_id)
        .values_list("entry_date", flat=True)
    )
    if today_iso not in dates:
        return 0
    streak = 0
    d = date.fromisoformat(today_iso)
    while d.isoformat() in dates:
        streak += 1
        d -= timedelta(days=1)
    return streak

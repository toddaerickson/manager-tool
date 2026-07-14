"""Template filters shared across pages.

New file justification: Django requires custom filters to live in a
`templatetags` package; formatting in each view would duplicate the
same date logic across every list/partial context.
"""

from datetime import date as _date

from django import template

register = template.Library()


@register.filter
def mmddyy(value):
    """Render an ISO 'YYYY-MM-DD' string (or date object) as MM/DD/YY.

    Date columns are TEXT in this schema (see CLAUDE.md date-shape
    decision), so templates receive strings. Garbage passes through
    unchanged rather than crashing the page.
    """
    if not value:
        return ""
    try:
        if isinstance(value, str):
            value = _date.fromisoformat(value[:10])
        return value.strftime("%m/%d/%y")
    except (ValueError, TypeError):
        return value

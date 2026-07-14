"""Views package — split from monolithic views.py for maintainability.

All public view functions are re-exported here so urls.py imports
(e.g., `from core import views as core_views`) stay stable.
"""

from core.views._common import (  # noqa: F401
    dashboard,
    health,
    hello,
    sentry_debug,
)
from core.views.events import dashboard_overview  # noqa: F401
from core.views.team import (  # noqa: F401
    team_members_add,
    team_members_delete,
    team_members_list,
    team_members_restore,
)
from core.views.events import (  # noqa: F401
    events_complete,
    events_delete,
    events_detail,
    events_edit,
    events_schedule,
    events_send_invite,
    events_upcoming,
)
from core.views.todos import (  # noqa: F401
    todos_add,
    todos_complete,
    todos_delegate,
    todos_delete,
    todos_edit,
    todos_list,
    todos_restore,
    todos_star,
    todos_uncomplete,
)
from core.views.journal import (  # noqa: F401
    journal_add,
    journal_coaching,
    journal_edit,
    journal_list,
)
from core.views.goals import (  # noqa: F401
    goals_add,
    goals_delete,
    goals_edit,
    goals_list,
)
from core.views.career import (  # noqa: F401
    career_dev,
    career_quarterly_review,
    convos_add,
    milestones_add,
    milestones_complete,
    plans_add,
    plans_update_status,
    skills_add,
    skills_delete,
)
from core.views.delegations import (  # noqa: F401
    delegations_add,
    delegations_delete,
    delegations_edit,
    delegations_list,
)
from core.views.decisions import (  # noqa: F401
    decisions_add,
    decisions_delete,
    decisions_edit,
    decisions_list,
)
from core.views.one_on_ones import (  # noqa: F401
    one_on_ones_add,
    one_on_ones_add_action,
    one_on_ones_prep_brief,
    one_on_ones_prep_brief_generate,
    one_on_ones_autosave,
    one_on_ones_complete,
    one_on_ones_delete,
    one_on_ones_detail,
    one_on_ones_list,
    one_on_ones_new,
)
from core.views.notes import (  # noqa: F401
    notes_add,
    notes_delete,
    notes_list,
)
from core.views.feedback import (  # noqa: F401
    feedback_add,
    feedback_delete,
    feedback_draft_sbi,
    feedback_list,
)
from core.views.settings_views import (  # noqa: F401
    settings_page,
    settings_send_digest,
)
from core.views.reference import (  # noqa: F401
    analytics,
    audit_log,
    history,
    resources,
)
from core.views.search import search_page  # noqa: F401
from core.views.inbox import (  # noqa: F401
    inbox_badge,
    inbox_list,
    inbox_quick_add,
    inbox_triage,
)

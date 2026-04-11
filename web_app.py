#!/usr/bin/env python3
"""
Manager Task Generator — Streamlit Web Application

Usage:
    streamlit run web_app.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

import json
import database as db
import templates
import coaching

# ---------------------------------------------------------------------------
# App config
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Manager Tool", page_icon="\U0001F4CB", layout="wide")
db.init_db()

# -- Database connection status banner --
_pg_failed, _pg_error = db.pg_connection_failed()
if _pg_failed:
    st.markdown(
        f'<div style="background:#2d0000;border:1px solid #ff4444;border-radius:8px;'
        f'padding:12px 16px;margin-bottom:16px;">'
        f'<span style="color:#ff4444;font-weight:700;font-size:1.1rem;">'
        f'\u26A0 Supabase connection failed</span><br>'
        f'<span style="color:#ff8888;font-size:0.85rem;">'
        f'Running on local SQLite — data will not persist across restarts.<br>'
        f'Error: {_pg_error[:120]}</span></div>',
        unsafe_allow_html=True,
    )

# -- Custom CSS for polished sidebar --
st.markdown("""
<style>
    /* Sidebar background */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #1a1a2e 0%, #16213e 100%);
    }
    [data-testid="stSidebar"] * {
        color: #e0e0e0 !important;
    }
    /* Sidebar title */
    [data-testid="stSidebar"] h3 {
        color: #ffffff !important;
        font-size: 1.4rem !important;
        padding-bottom: 0 !important;
    }
    /* Active nav button (primary) */
    [data-testid="stSidebar"] button[kind="primary"] {
        background-color: #4F8BF9 !important;
        color: #ffffff !important;
        border: none !important;
        font-weight: 600 !important;
    }
    /* Inactive nav button (secondary) */
    [data-testid="stSidebar"] button[kind="secondary"] {
        background-color: transparent !important;
        color: #c0c0c0 !important;
        border: 1px solid #2a2a4a !important;
        text-align: left !important;
    }
    [data-testid="stSidebar"] button[kind="secondary"]:hover {
        background-color: #2a2a4a !important;
        color: #ffffff !important;
        border-color: #4F8BF9 !important;
    }
    /* Expander headers */
    [data-testid="stSidebar"] [data-testid="stExpander"] summary {
        font-size: 0.85rem !important;
        color: #8888aa !important;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] summary:hover {
        color: #ffffff !important;
    }
    /* Dividers */
    [data-testid="stSidebar"] hr {
        border-color: #2a2a4a !important;
    }
    /* Streak badge */
    [data-testid="stSidebar"] .stMarkdown p strong {
        color: #f9a825 !important;
    }
    /* Compact nav buttons — tight spacing */
    [data-testid="stSidebar"] .stButton {
        margin-bottom: -0.6rem !important;
    }
    [data-testid="stSidebar"] .stButton button {
        padding: 0.2rem 0.5rem !important;
        font-size: 0.82rem !important;
        min-height: 1.8rem !important;
        margin: 0 !important;
    }
    /* Section labels */
    [data-testid="stSidebar"] .stCaption {
        font-size: 0.6rem !important;
        letter-spacing: 0.1em !important;
        text-transform: uppercase !important;
        margin-top: 0.4rem !important;
        margin-bottom: -0.4rem !important;
        padding-bottom: 0 !important;
        opacity: 0.5 !important;
    }
    /* Reduce general sidebar vertical spacing */
    [data-testid="stSidebar"] .stMarkdown {
        margin-bottom: -0.5rem !important;
    }
    [data-testid="stSidebar"] hr {
        margin: 0.4rem 0 !important;
    }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def get_current_manager_id():
    """Return the logged-in manager's ID, or None for unauthenticated use."""
    return st.session_state.get("manager_id") or None


# Shorthand for threading manager_id into DB calls
_mid = get_current_manager_id


def require_auth():
    """Show login/register screen if not authenticated. Returns True if authed."""
    if "manager_id" in st.session_state and st.session_state["manager_id"]:
        return True
    _show_auth_screen()
    return False


def _show_auth_screen():
    """Render the login / registration screen."""
    st.title("\U0001F4CB Manager Tool")
    st.caption("Your private management coaching journal")

    tab_login, tab_register = st.tabs(["Log In", "Create Account"])

    with tab_login:
        # Rate limiting: block after 5 failed attempts within 15 minutes
        attempts = st.session_state.get("login_attempts", [])
        cutoff = datetime.now() - timedelta(minutes=15)
        attempts = [t for t in attempts if t > cutoff]
        st.session_state["login_attempts"] = attempts
        locked = len(attempts) >= 5

        if locked:
            st.error("Too many failed attempts. Please wait a few minutes.")

        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Log In", use_container_width=True):
                if locked:
                    st.error("Account temporarily locked. Try again later.")
                elif username and password:
                    manager = db.authenticate_manager(username, password)
                    if manager:
                        st.session_state["login_attempts"] = []
                        st.session_state["manager_id"] = manager["id"]
                        st.session_state["manager_name"] = manager["display_name"]
                        st.rerun()
                    else:
                        st.session_state.setdefault("login_attempts", []).append(datetime.now())
                        st.error("Invalid username or password.")
                else:
                    st.warning("Enter both username and password.")

    with tab_register:
        with st.form("register_form"):
            new_user = st.text_input("Choose a username")
            new_name = st.text_input("Your name")
            new_email = st.text_input("Email (optional)")
            new_pw = st.text_input("Password", type="password", key="reg_pw")
            new_pw2 = st.text_input("Confirm password", type="password")

            rc1, rc2 = st.columns(2)
            with rc1:
                days_options = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                work_days = st.multiselect("Work days", days_options,
                    default=["Mon", "Tue", "Wed", "Thu", "Fri"])
            with rc2:
                start_time = st.time_input("Work start", value=datetime.strptime("09:00", "%H:%M").time())
                end_time = st.time_input("Work end", value=datetime.strptime("17:00", "%H:%M").time())

            if st.form_submit_button("Create Account", use_container_width=True):
                if not new_user or not new_name or not new_pw:
                    st.warning("Username, name, and password are required.")
                elif len(new_pw) < 8:
                    st.error("Password must be at least 8 characters.")
                elif new_pw != new_pw2:
                    st.error("Passwords don't match.")
                elif db.manager_exists(new_user):
                    st.error("Username already taken.")
                else:
                    schedule = json.dumps({
                        "days": work_days,
                        "start": start_time.strftime("%H:%M"),
                        "end": end_time.strftime("%H:%M"),
                    })
                    mid = db.create_manager(new_user, new_name, new_pw,
                        email=new_email or None, work_schedule=schedule)
                    if mid:
                        st.session_state["manager_id"] = mid
                        st.session_state["manager_name"] = new_name
                        st.success(f"Welcome, {new_name}! Redirecting...")
                        st.rerun()
                    else:
                        st.error("Failed to create account.")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def navigate(page_name):
    """Set the active page in session state."""
    st.session_state["nav_page"] = page_name


def df_from(rows, columns=None):
    """Convert list-of-dicts to a DataFrame, selecting columns if given."""
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    if columns:
        columns = [c for c in columns if c in frame.columns]
        frame = frame[columns]
    frame.columns = [c.replace("_", " ").title() for c in frame.columns]
    return frame


def member_options():
    """Return (display_names_list, name_to_id_dict)."""
    members = db.list_team_members(manager_id=_mid())
    mapping = {m["name"]: m["id"] for m in members}
    return list(mapping.keys()), mapping


def render_coaching_pane(notes_text, context_type="journal", member_name=None,
                         event_type=None, prep_data=None, key_suffix="",
                         journal_entry_id=None):
    """Render the AI coaching right-pane for any page.
    Call this inside a right column. Uses session_state to cache responses.
    If journal_entry_id is provided, saves the coaching response to the entry."""
    st.markdown("### \U0001F9E0 Coaching Corner")
    state_key = f"coaching_response_{context_type}_{key_suffix}"

    if st.button("\U0001F4A1 Coach me on this", key=f"coach_btn_{key_suffix}",
                 use_container_width=True):
        if notes_text and notes_text.strip():
            with st.spinner("Thinking..."):
                response = coaching.get_coaching_response(
                    notes_text, context_type, member_name, event_type, prep_data)
                st.session_state[state_key] = response
                # Persist coaching response to journal entry if available
                if journal_entry_id and response:
                    db.update_journal_entry(journal_entry_id,
                                            coaching_response=response)
        else:
            st.session_state[state_key] = (
                "*Write some notes first, then ask for coaching.*")

    # Show cached response
    if state_key in st.session_state and st.session_state[state_key]:
        st.markdown(st.session_state[state_key])
    else:
        st.caption(
            "Type your notes on the left, then click **Coach me on this** "
            "for questions, frameworks, and devil's advocate challenges "
            "drawn from 23 management books."
        )


def show_toast():
    """Display and clear any pending toast message from session_state."""
    if "toast" in st.session_state:
        kind, msg = st.session_state.pop("toast")
        if kind == "success":
            st.success(msg)
        elif kind == "error":
            st.error(msg)
        elif kind == "warning":
            st.warning(msg)


def set_toast(kind, msg):
    st.session_state["toast"] = (kind, msg)


# ---------------------------------------------------------------------------
# Confirmation dialogs  (#2)
# ---------------------------------------------------------------------------

@st.dialog("Confirm: Complete Event")
def confirm_complete_event(event_id, title):
    st.write(f"Mark **{title}** (#{event_id}) as completed?")
    notes = st.text_area("Meeting notes (optional)")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Complete", type="primary", use_container_width=True):
            db.complete_event(int(event_id), notes=notes or None)
            st.toast(f"Event #{event_id} completed.", icon="\u2705")
            st.rerun()
    with c2:
        if st.button("Cancel", use_container_width=True):
            st.rerun()


@st.dialog("Confirm: Complete Action Item")
def confirm_complete_action(action_id, description):
    st.write(f"Mark action item **#{action_id}** as completed?")
    st.caption(description)
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Complete", type="primary", use_container_width=True):
            db.complete_action_item(int(action_id))
            st.toast(f"Action #{action_id} completed.", icon="\u2705")
            st.rerun()
    with c2:
        if st.button("Cancel", use_container_width=True):
            st.rerun()


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def page_dashboard():
    st.title("Dashboard")
    show_toast()

    # -- Daily Coach Suggestion (personalized first action) --
    if _mid():
        suggestion = coaching.get_daily_suggestion(_mid())
        if suggestion and suggestion.get("suggestion"):
            tier_icon = "\U0001F9E0" if suggestion.get("tier") == "ai" else "\U0001F4A1"
            sc1, sc2 = st.columns([6, 1])
            with sc1:
                st.markdown(
                    f'<div style="background:linear-gradient(90deg,#1a1a2e,#16213e);'
                    f'border-left:4px solid #4F8BF9;border-radius:8px;padding:14px 18px;'
                    f'margin-bottom:16px;">'
                    f'<span style="color:#4F8BF9;font-weight:600;">'
                    f'{tier_icon} Coach</span><br>'
                    f'<span style="color:#e0e0e0;font-style:italic;">'
                    f'{suggestion["suggestion"]}</span></div>',
                    unsafe_allow_html=True,
                )
            with sc2:
                action_page = suggestion.get("action_page")
                if action_page:
                    if st.button("Go", key="coach_go", use_container_width=True):
                        navigate(action_page)
                        st.rerun()
                if st.button("Got it", key="coach_dismiss", use_container_width=True):
                    db.dismiss_todays_suggestion(_mid())
                    st.rerun()

    # -- Daily Wisdom (variable reward — different every day) --
    wisdom = templates.get_daily_wisdom()
    st.info(f"\U0001F4A1 **Daily Wisdom #{wisdom['number']}:** {wisdom['text']}")

    # -- Streaks (loss aversion) --
    streak = db.get_journal_streak(manager_id=_mid())
    if streak > 0:
        st.markdown(f"\U0001F525 **Journal streak: {streak} day{'s' if streak != 1 else ''}**")

    # -- Nudges (triggers for action) --
    nudges = db.get_nudges(manager_id=_mid())
    if nudges:
        for n in nudges:
            if n["severity"] == "critical":
                st.error(f"{n['message']}")
            elif n["severity"] == "warning":
                st.warning(f"{n['message']}")
            else:
                st.info(f"{n['message']}")

    # -- Anti-pattern alert (identity hook) --
    meeting_data = db.get_time_since_last_event_per_member(manager_id=_mid())
    feedback_data = db.get_feedback_ratios(manager_id=_mid())
    ap = templates.detect_anti_patterns(meeting_data, feedback_data)
    if ap:
        p = ap[0]
        st.warning(f"**{p['pattern']}:** {p['evidence']} — {p['suggestion']}")

    # -- Delegation & decision review nudges --
    overdue_dels = db.get_overdue_delegations(manager_id=_mid())
    if overdue_dels:
        st.warning(f"\U0001F4E4 **{len(overdue_dels)} delegation(s) past check-in date** — "
                   f"review them in Delegations.")
    decisions_due = db.get_decisions_due_for_review(manager_id=_mid())
    if decisions_due:
        st.info(f"\U0001F9E0 **{len(decisions_due)} decision(s) due for review** — "
                f"did they play out as expected? Check the Decision Log.")

    # -- Quick stats [C7: System 1 — emoji indicators for instant scanning] --
    summary = db.get_weekly_summary(manager_id=_mid())
    c1, c2, c3, c4 = st.columns(4)
    upcoming_n = len(summary["upcoming_events"])
    pending_n = len(summary["pending_actions"])
    overdue_n = len(summary["overdue_actions"])
    c1.metric("\U0001F4C5 Upcoming", upcoming_n)
    c2.metric("\u2705 Completed", len(summary["completed_events"]))
    c3.metric("\u26A0\uFE0F Pending" if pending_n > 0 else "\U0001F4CB Pending", pending_n)
    c4.metric("\U0001F525 Streak", f"{streak} days")

    # -- Quick-action cards (#5) --
    st.subheader("Quick Actions")
    qa1, qa2, qa3, qa4 = st.columns(4)
    with qa1:
        if st.button("\U0001F4C5  Schedule 1:1", use_container_width=True):
            navigate("Schedule Event")
            st.rerun()
    with qa2:
        if st.button("\U0001F4DD  Log Feedback", use_container_width=True):
            navigate("Record Feedback")
            st.rerun()
    with qa3:
        if st.button("\U0001F464  Add Member", use_container_width=True):
            navigate("Add Member")
            st.rerun()
    with qa4:
        if st.button("\U0001F6A8  View Overdue", use_container_width=True):
            navigate("Action Items")
            st.rerun()

    # -- Tables --
    if summary["upcoming_events"]:
        st.subheader("Upcoming Events This Week")
        st.dataframe(
            df_from(summary["upcoming_events"],
                    ["id", "title", "event_type", "scheduled_date",
                     "scheduled_time", "participant_name"]),
            use_container_width=True, hide_index=True,
        )

    if summary["pending_actions"]:
        st.subheader("Pending Action Items")
        st.dataframe(
            df_from(summary["pending_actions"],
                    ["id", "description", "assignee", "due_date", "status"]),
            use_container_width=True, hide_index=True,
        )

    if summary["overdue_actions"]:
        st.subheader(":red[Overdue Action Items]")
        st.dataframe(
            df_from(summary["overdue_actions"],
                    ["id", "description", "assignee", "due_date"]),
            use_container_width=True, hide_index=True,
        )

    # -- Onboarding checklist (endowed progress) — only for new users --
    members = db.list_team_members(manager_id=_mid())
    all_events = db.list_events(limit=5, manager_id=_mid())
    all_feedback = db.list_feedback()
    journal_entries = db.list_journal_entries(limit=1, manager_id=_mid())
    if len(members) < 2 and len(all_events) < 3:
        st.divider()
        st.subheader("Getting Started")
        steps = [
            ("\u2705", "Open the app", True),
            ("\u2705" if members else "\u2B1C", "Add your first team member", bool(members)),
            ("\u2705" if all_events else "\u2B1C", "Schedule a 1-on-1", bool(all_events)),
            ("\u2705" if all_feedback else "\u2B1C", "Record feedback using SBI", bool(all_feedback)),
            ("\u2705" if journal_entries else "\u2B1C", "Write your first journal entry", bool(journal_entries)),
        ]
        done = sum(1 for _, _, d in steps if d)
        st.progress(done / len(steps), text=f"{done}/{len(steps)} complete")
        for icon, label, _ in steps:
            st.markdown(f"{icon} {label}")

    # -- [C3: Peak-End Rule] End on a positive note, not overdue items --
    st.divider()
    st.markdown(f"\U0001F4A1 *{wisdom['text'][:200]}*")


# -- Activities -------------------------------------------------------------

def page_schedule_event():
    st.title("Schedule an Event")

    type_keys = list(templates.EVENT_TYPES.keys())
    type_labels = [templates.EVENT_TYPES[t]["label"] for t in type_keys]
    names, name_map = member_options()

    # Single-column form for mobile (#7)
    with st.form("schedule_form"):
        event_label = st.selectbox("Event Type", type_labels)
        title = st.text_input("Title (leave blank for default)")
        participant = st.selectbox("Participant", ["(none)"] + names)
        date = st.date_input("Date", value=datetime.now().date())
        time_val = st.time_input("Time",
                                 value=datetime.strptime("10:00", "%H:%M").time())
        duration = st.number_input("Duration (min)", value=30, min_value=5, step=5)
        location = st.text_input("Location / meeting link")
        gen_agenda = st.checkbox("Generate agenda from template")
        submitted = st.form_submit_button("Schedule Event", use_container_width=True)

    if submitted:
        idx = type_labels.index(event_label)
        event_type = type_keys[idx]
        member_name = participant if participant != "(none)" else None
        member_id = name_map.get(participant) if member_name else None
        final_title = title or templates.get_default_title(event_type, member_name)
        agenda = (templates.generate_agenda(event_type, member_name)
                  if gen_agenda else None)

        eid = db.create_event(
            title=final_title, event_type=event_type,
            scheduled_date=date.strftime("%Y-%m-%d"),
            scheduled_time=time_val.strftime("%H:%M"),
            team_member_id=member_id,
            duration_minutes=duration,
            location=location or None,
            agenda=agenda,
            manager_id=_mid(),
        )
        st.toast(f"Event #{eid} scheduled: {final_title}", icon="\U0001F4C5")
        st.rerun()


def page_upcoming_events():
    st.title("Upcoming Events (Next 14 Days)")

    events = db.get_upcoming_events(days=14, manager_id=_mid())
    if events:
        st.dataframe(
            df_from(events, ["id", "title", "event_type", "scheduled_date",
                             "scheduled_time", "participant_name", "status"]),
            use_container_width=True, hide_index=True,
        )
        ev_left, ev_right = st.columns([3, 2])
        with ev_left:
            with st.form("complete_event"):
                eid = st.number_input("Event ID to complete", min_value=1, step=1)
                notes = st.text_area("Meeting notes", height=150,
                    placeholder="What happened? What did you observe? What do you need to follow up on?")
                if st.form_submit_button("Mark Complete"):
                    event = db.get_event(int(eid))
                    if event:
                        db.complete_event(int(eid), notes=notes or None)
                        set_toast("success", f"Event #{eid} marked completed.")
                    else:
                        set_toast("error", f"Event #{eid} not found.")
                    st.rerun()
        with ev_right:
            # Try to get context from selected event
            ev_notes = st.session_state.get("ev_notes_text", "")
            render_coaching_pane(notes if notes else "",
                context_type="event_completion",
                event_type="meeting",
                key_suffix="event_complete")
    else:
        st.info("No upcoming events scheduled.")
        return

    # Inline editing with data_editor (#3)
    edit_df = pd.DataFrame(events)
    edit_df["complete"] = False
    display_cols = ["complete", "id", "title", "event_type", "scheduled_date",
                    "scheduled_time", "participant_name"]
    display_cols = [c for c in display_cols if c in edit_df.columns]
    view = edit_df[display_cols].copy()
    view.columns = [c.replace("_", " ").title() for c in view.columns]

    edited = st.data_editor(
        view,
        use_container_width=True,
        hide_index=True,
        disabled=[c for c in view.columns if c != "Complete"],
        column_config={
            "Complete": st.column_config.CheckboxColumn("Done?", default=False),
        },
        key="upcoming_editor",
    )

    # Check for rows the user ticked
    checked = edited[edited["Complete"]].reset_index(drop=True)
    if not checked.empty:
        row = checked.iloc[0]
        eid = int(row["Id"])
        title = row["Title"]
        confirm_complete_event(eid, title)


def page_event_history():
    st.title("Event History")

    # Search and filtering (#4)
    f1, f2, f3 = st.columns(3)
    with f1:
        search = st.text_input("Search by title", key="eh_search")
    with f2:
        type_filter = st.selectbox(
            "Event type",
            ["All"] + list(templates.EVENT_TYPES.keys()),
            key="eh_type",
        )
    with f3:
        date_range = st.date_input(
            "Date range", value=[], key="eh_dates",
            help="Select start and end date to filter",
        )

    kwargs = {"status": "completed", "limit": 100}
    if type_filter != "All":
        kwargs["event_type"] = type_filter
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        kwargs["from_date"] = date_range[0].strftime("%Y-%m-%d")
        kwargs["to_date"] = date_range[1].strftime("%Y-%m-%d")

    kwargs["manager_id"] = _mid()
    events = db.list_events(**kwargs)

    if search:
        events = [e for e in events
                  if search.lower() in (e.get("title") or "").lower()]

    if events:
        st.dataframe(
            df_from(events, ["id", "title", "event_type", "scheduled_date",
                             "participant_name", "notes"]),
            use_container_width=True, hide_index=True,
        )
        st.caption(f"{len(events)} event(s) found")
    else:
        st.info("No completed events match your filters.")


# -- People -----------------------------------------------------------------

def page_team_roster():
    st.title("Team Roster")

    members = db.list_team_members(manager_id=_mid())

    # -- Inline Add Member form (collapsible) --
    with st.expander("Add New Team Member", expanded=not members):
        with st.form("add_member"):
            ac1, ac2 = st.columns(2)
            with ac1:
                name = st.text_input("Full Name *")
                email = st.text_input("Email")
            with ac2:
                role = st.text_input("Role / Title")
                start_date = st.date_input("Start Date", value=datetime.now().date())
            notes = st.text_input("Notes")
            submitted = st.form_submit_button("Add Member", use_container_width=True)
        if submitted:
            if not name:
                st.error("Name is required.")
            else:
                db.add_team_member(
                    name, email or None, role or None,
                    start_date.strftime("%Y-%m-%d"), notes or None,
                    manager_id=_mid(),
                )
                st.toast(f"Added {name}", icon="\u2705")
                st.rerun()

    if not members:
        return

    # Search
    search = st.text_input("Search by name, email, or role", key="tr_search")
    if search:
        q = search.lower()
        members = [m for m in members
                   if q in (m.get("name") or "").lower()
                   or q in (m.get("email") or "").lower()
                   or q in (m.get("role") or "").lower()]

    if not members:
        st.warning("No members match your search.")
        return

    st.dataframe(
        df_from(members, ["id", "name", "email", "role", "start_date"]),
        use_container_width=True, hide_index=True,
    )

    mid = st.selectbox(
        "Select a member for details",
        options=[m["id"] for m in members],
        format_func=lambda x: next(
            (f"{m['name']} (ID {m['id']})" for m in members if m["id"] == x),
            str(x),
        ),
    )

    if st.button("View Details"):
        st.session_state["detail_member_id"] = mid

    if "detail_member_id" in st.session_state:
        summary = db.get_member_summary(st.session_state["detail_member_id"])
        if summary:
            _render_member_detail(summary)


def _render_member_detail(summary):
    m = summary["member"]
    st.divider()
    st.subheader(f"Member: {m['name']}")
    st.markdown(
        f"**Role:** {m.get('role', 'N/A')} &nbsp;&nbsp; "
        f"**Email:** {m.get('email', 'N/A')} &nbsp;&nbsp; "
        f"**Start:** {m.get('start_date', 'N/A')}"
    )
    if m.get("notes"):
        st.markdown(f"**Notes:** {m['notes']}")

    if summary["recent_events"]:
        st.markdown("**Recent Events**")
        st.dataframe(
            df_from(summary["recent_events"],
                    ["id", "title", "event_type", "scheduled_date", "status"]),
            use_container_width=True, hide_index=True,
        )
    if summary["goals"]:
        st.markdown("**Goals**")
        st.dataframe(
            df_from(summary["goals"], ["id", "quarter", "description", "status"]),
            use_container_width=True, hide_index=True,
        )
    if summary["feedback"]:
        st.markdown("**Feedback History**")
        for fb in summary["feedback"][:5]:
            color = "green" if fb["feedback_type"] == "positive" else "orange"
            fb_col, del_col = st.columns([5, 1])
            with fb_col:
                st.markdown(
                    f":{color}[**{fb['feedback_type'].upper()}**] "
                    f"— {fb['created_at'][:10]}  \n"
                    f"&nbsp;&nbsp;**S:** {fb.get('situation', 'N/A')}  \n"
                    f"&nbsp;&nbsp;**B:** {fb.get('behavior', 'N/A')}  \n"
                    f"&nbsp;&nbsp;**I:** {fb.get('impact', 'N/A')}"
                )
            with del_col:
                if st.button("Delete", key=f"del_fb_{fb['id']}"):
                    db.delete_feedback(fb["id"])
                    set_toast("success", f"Feedback #{fb['id']} deleted.")
                    st.rerun()


# -- Tracking ---------------------------------------------------------------

def page_action_items():
    st.title("Action Items")

    # -- Inline Add form --
    with st.expander("Add Action Item"):
        with st.form("add_action"):
            ac1, ac2 = st.columns(2)
            with ac1:
                desc = st.text_input("Description *")
                assignee = st.text_input("Assignee")
            with ac2:
                due_date = st.date_input("Due Date", value=None)
                event_id = st.text_input("Related Event ID (optional)")
            if st.form_submit_button("Add Action Item", use_container_width=True):
                if not desc:
                    st.error("Description is required.")
                else:
                    eid = int(event_id) if event_id and event_id.isdigit() else None
                    due = due_date.strftime("%Y-%m-%d") if due_date else None
                    db.add_action_item(desc, event_id=eid,
                                       assignee=assignee or None, due_date=due,
                                       manager_id=_mid())
                    st.toast("Action item added.", icon="\u2705")
                    st.rerun()

    actions = db.get_pending_action_items(manager_id=_mid())
    if not actions:
        st.success("No pending action items. You're caught up!")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    overdue = [a for a in actions if a.get("due_date") and a["due_date"] < today]
    if overdue:
        st.warning(f"{len(overdue)} overdue action item(s)!")

    # Inline editing with data_editor (#3)
    edit_df = pd.DataFrame(actions)
    edit_df["complete"] = False
    display_cols = ["complete", "id", "description", "assignee", "due_date",
                    "status", "event_title"]
    display_cols = [c for c in display_cols if c in edit_df.columns]
    view = edit_df[display_cols].copy()
    view.columns = [c.replace("_", " ").title() for c in view.columns]

    edited = st.data_editor(
        view,
        use_container_width=True,
        hide_index=True,
        disabled=[c for c in view.columns if c != "Complete"],
        column_config={
            "Complete": st.column_config.CheckboxColumn("Done?", default=False),
        },
        key="action_editor",
    )

    checked = edited[edited["Complete"]].reset_index(drop=True)
    if not checked.empty:
        row = checked.iloc[0]
        confirm_complete_action(int(row["Id"]), row["Description"])

    # Delete action item
    with st.expander("Delete an action item"):
        del_id = st.number_input("Action item ID to delete", min_value=1, step=1,
                                 key="del_action_id")
        if st.button("Delete Action Item", key="del_action_btn"):
            db.delete_action_item(int(del_id))
            set_toast("success", f"Action item #{del_id} deleted.")
            st.rerun()


def page_record_feedback():
    st.title("Record Feedback (SBI Framework)")

    names, name_map = member_options()
    if not names:
        st.warning("No team members yet. Add one first.")
        return

    fb_left, fb_right = st.columns([3, 2])

    with fb_left:
        st.info(
            "**SBI Framework**  \n"
            "**Situation:** When/where did this happen?  \n"
            "**Behavior:** What specifically did they do?  \n"
            "**Impact:** What was the result/effect?"
        )
        with st.form("feedback_form"):
            member_name = st.selectbox("Team Member", names)
            fb_type = st.radio("Feedback Type", ["Positive", "Constructive"], horizontal=True)
            situation = st.text_input("Situation")
            behavior = st.text_input("Behavior")
            impact = st.text_input("Impact")
            submitted = st.form_submit_button("Save Feedback")

        if submitted:
            mid = name_map.get(member_name)
            if not mid:
                st.error("Select a team member.")
            else:
                fb = "positive" if fb_type == "Positive" else "constructive"
                fid = db.add_feedback(mid, fb, situation or None, behavior or None, impact or None)
                set_toast("success", f"Feedback #{fid} recorded.")
                st.rerun()

    with fb_right:
        # Build context from what's been entered
        fb_context = f"{situation or ''} {behavior or ''} {impact or ''}"
        render_coaching_pane(
            fb_context,
            context_type="feedback",
            member_name=names[0] if names else None,
            key_suffix="feedback")


# -- Goals ------------------------------------------------------------------

def page_quarterly_goals():
    st.title("Quarterly Goals")

    # -- Inline Add Goal form --
    names, name_map = member_options()
    if names:
        with st.expander("Add New Goal"):
            now = datetime.now()
            q = (now.month - 1) // 3 + 1
            default_quarter = f"Q{q} {now.year}"
            with st.form("add_goal"):
                gc1, gc2 = st.columns(2)
                with gc1:
                    member_name = st.selectbox("Team Member", names)
                    quarter = st.text_input("Quarter", value=default_quarter)
                with gc2:
                    description = st.text_input("Goal Description *")
                    key_results = st.text_area("Key Results (one per line)", height=68)
                if st.form_submit_button("Add Goal", use_container_width=True):
                    mid_g = name_map.get(member_name)
                    if not mid_g:
                        st.error("Select a team member.")
                    elif not description:
                        st.error("Description is required.")
                    else:
                        db.add_goal(mid_g, quarter, description, key_results or None)
                        st.toast(f"Goal added for {member_name}.", icon="\u2705")
                        st.rerun()

    goals = db.list_goals(manager_id=_mid())
    if not goals:
        st.info("No goals set yet.")
        return

    st.dataframe(
        df_from(goals, ["id", "member_name", "quarter", "description", "status"]),
        use_container_width=True, hide_index=True,
    )
    statuses = ["not_started", "in_progress", "met", "exceeded",
                "partially_met", "not_met"]

    # Single-column form (#7)
    uc1, uc2 = st.columns(2)
    with uc1:
        with st.form("update_goal"):
            gid = st.number_input("Goal ID to update", min_value=1, step=1)
            new_status = st.selectbox("New Status", statuses)
            if st.form_submit_button("Update Status", use_container_width=True):
                db.update_goal(int(gid), status=new_status)
                st.toast(f"Goal #{gid} updated to '{new_status}'.", icon="\u2705")
                st.rerun()
    with uc2:
        with st.form("delete_goal"):
            del_gid = st.number_input("Goal ID to delete", min_value=1, step=1,
                                       key="del_goal_id")
            if st.form_submit_button("Delete Goal", use_container_width=True):
                db.delete_goal(int(del_gid))
                st.toast(f"Goal #{del_gid} deleted.", icon="\u2705")
                st.rerun()


# -- Resources --------------------------------------------------------------

def page_resources():
    st.title("Resources")

    tab_agendas, tab_tips, tab_patterns = st.tabs([
        "Agenda Templates", "Management Tips", "Anti-Patterns"
    ])

    with tab_agendas:
        type_options = ["check_in", "coaching", "one_on_one", "quarterly_review"]
        type_labels = [templates.EVENT_TYPES[t]["label"] for t in type_options]

        label = st.selectbox("Meeting Type", type_labels)
        name = st.text_input("Participant Name", value="Team Member")

        idx = type_labels.index(label)
        event_type = type_options[idx]
        agenda = templates.generate_agenda(event_type, name or None)
        st.code(agenda, language=None)

        if event_type == "one_on_one":
            with st.expander("Topic Bank — Conversation Starters"):
                topics = templates.get_topic_suggestions()
                for category, questions in topics.items():
                    st.markdown(f"**{category}**")
                    for q in questions:
                        st.markdown(f"- {q}")

    with tab_tips:
        tips = templates.get_tips_by_count(8)
        for i, tip in enumerate(tips, 1):
            st.markdown(f"**{i}.** {tip}")

        with st.expander("Weekly Manager Self-Assessment"):
            for dim, question in templates.SELF_ASSESSMENT_DIMENSIONS:
                st.markdown(f"**{dim}** — {question} &nbsp; ___/5")

    with tab_patterns:
        st.subheader("Common Anti-Patterns")
        for ap in templates.ANTI_PATTERNS:
            with st.container():
                st.markdown(f":red[**{ap['name']}**]")
                st.markdown(f"*Symptom:* {ap['symptom']}")
                st.markdown(f":green[*Fix:* {ap['fix']}]")
                st.markdown("---")


# -- Settings ---------------------------------------------------------------

def page_configuration():
    st.title("Email & Profile Configuration")

    st.info(
        "**AI Coaching** — Enter your Anthropic API key to enable the "
        "Claude-powered coaching sidebar. Without it, the tool uses "
        "the local 620-idea wisdom library for coaching prompts."
    )
    with st.form("api_key_form"):
        api_key = st.text_input(
            "Anthropic API Key",
            value="",
            type="password",
            placeholder="sk-ant-... (leave blank to keep existing key)",
        )
        if st.form_submit_button("Save API Key"):
            if api_key:
                db.set_config("anthropic_api_key", api_key)
                set_toast("success", "API key saved. AI coaching is now active.")
                st.rerun()
    has_key = db.get_config("anthropic_api_key")
    if has_key:
        st.success("AI coaching: **Active** (API key configured)")
    else:
        st.caption("AI coaching: Offline mode (using local wisdom library)")

    st.divider()
    st.info(
        "**SMTP Setup for Google Calendar Invitations**  \n"
        "To send invites via Gmail, generate an App Password at "
        "https://myaccount.google.com/apppasswords and use it below."
    )

    # Single-column form (#7)
    with st.form("config_form"):
        name = st.text_input("Your Name",
                             value=db.get_config("manager_name", ""))
        email = st.text_input("Email Address",
                              value=db.get_config("manager_email", ""))
        smtp_server = st.text_input(
            "SMTP Server", value=db.get_config("smtp_server", "smtp.gmail.com"))
        smtp_port = st.text_input(
            "SMTP Port", value=db.get_config("smtp_port", "587"))
        smtp_user = st.text_input(
            "SMTP Username", value=db.get_config("smtp_user", ""))
        smtp_password = st.text_input(
            "SMTP Password / App Password", type="password")
        submitted = st.form_submit_button("Save Configuration",
                                          use_container_width=True)

    if submitted:
        db.set_config("manager_name", name)
        db.set_config("manager_email", email)
        db.set_config("smtp_server", smtp_server)
        db.set_config("smtp_port", smtp_port)
        db.set_config("smtp_user", smtp_user)
        if smtp_password:
            db.set_config("smtp_password", smtp_password)
        st.toast("Configuration saved.", icon="\u2705")
        st.rerun()

    st.subheader("Current Configuration")
    config = db.get_all_config()
    if config:
        for key, value in config.items():
            display = "********" if "password" in key or "secret" in key else value
            st.text(f"{key}: {display}")
    else:
        st.caption("No configuration set yet.")

    # -- Weekly Digest --
    st.divider()
    st.subheader("Weekly Email Digest")
    st.caption(
        "Send yourself a summary of upcoming events, overdue actions, "
        "nudges, and your journal streak."
    )
    if st.button("Send Weekly Digest Now", use_container_width=True):
        import calendar_service
        ok, msg = calendar_service.send_weekly_digest(manager_id=_mid())
        if ok:
            st.success(msg)
        else:
            st.error(msg)


# ---------------------------------------------------------------------------
# New Pages: Journal, Timeline, Analytics, Career Development
# ---------------------------------------------------------------------------

def page_journal():
    st.title("Manager Journal")
    show_toast()

    # -- Streak & daily wisdom (top bar) --
    streak = db.get_journal_streak(manager_id=_mid())
    wisdom = templates.get_daily_wisdom()
    c1, c2 = st.columns([1, 3])
    with c1:
        flame = "\U0001F525" if streak > 0 else ""
        st.metric("Journal Streak", f"{flame} {streak} day{'s' if streak != 1 else ''}")
    with c2:
        st.info(f"**Daily Wisdom #{wisdom['number']}:** {wisdom['text']}")

    # -- Mood/energy sparkline --
    entries = db.list_journal_entries(limit=14, manager_id=_mid())
    if entries:
        chart_data = pd.DataFrame([
            {"Date": e["entry_date"], "Mood": e["mood"], "Energy": e["energy"]}
            for e in reversed(entries) if e.get("mood")
        ])
        if not chart_data.empty:
            chart_data = chart_data.set_index("Date")
            st.line_chart(chart_data, height=120)

    # -- Tabs --
    tab_today, tab_weekly, tab_history = st.tabs([
        "Today", "Weekly Reflection", "Journal History"
    ])

    with tab_today:
        today_str = datetime.now().date().isoformat()
        existing = db.get_journal_entry_by_date(today_str, "daily", manager_id=_mid())

        # Two-pane layout: notes on left, coaching on right
        left_col, right_col = st.columns([3, 2])

        with left_col:
            with st.form("journal_daily"):
                content = st.text_area(
                    "What's on your mind?",
                    value=existing["content"] if existing else "",
                    height=180,
                    placeholder="Reflect on your day... no rules, no required fields.",
                )
                jc1, jc2 = st.columns(2)
                with jc1:
                    mood_labels = {1: "1 \U0001F62B", 2: "2 \U0001F615", 3: "3 \U0001F610",
                                   4: "4 \U0001F642", 5: "5 \U0001F525"}
                    mood = st.select_slider("Mood", options=[1, 2, 3, 4, 5],
                        value=existing["mood"] if existing and existing["mood"] else 3,
                        format_func=lambda x: mood_labels[x])
                with jc2:
                    energy_labels = {1: "1 \U0001FAAB", 2: "2 \U0001F50B", 3: "3 \U0001F50B",
                                     4: "4 \U0001F50B", 5: "5 \u26A1"}
                    energy = st.select_slider("Energy", options=[1, 2, 3, 4, 5],
                        value=existing["energy"] if existing and existing["energy"] else 3,
                        format_func=lambda x: energy_labels[x])
                with st.expander("Private coaching notes"):
                    private = st.text_area(
                        "What are you working on about yourself?",
                        value=existing["private_notes"] if existing else "",
                        height=80,
                        label_visibility="collapsed",
                    )
                tags = st.text_input("Tags (comma-separated)",
                    value=existing["tags"] if existing else "")
                submitted = st.form_submit_button("Save Entry")

            if submitted:
                if existing:
                    db.update_journal_entry(existing["id"], content=content, mood=mood,
                        energy=energy, private_notes=private, tags=tags)
                else:
                    db.add_journal_entry(today_str, "daily", content, mood, energy, private, tags,
                                         manager_id=_mid())
                if content and content.strip():
                    matched = templates.match_wisdom_to_text(content, count=1)
                    if matched:
                        st.success(f"\u2728 **Saved.** Here's something relevant:")
                        st.markdown(f"> {matched[0]['text']}")
                else:
                    st.success("Entry saved.")

        with right_col:
            # Get current text from existing entry or empty
            current_text = (existing["content"] if existing else "") or ""
            entry_id = existing["id"] if existing else None
            render_coaching_pane(current_text, context_type="journal",
                                key_suffix="journal_daily",
                                journal_entry_id=entry_id)

    with tab_weekly:
        # Find current week's Monday
        today = datetime.now().date()
        monday = (today - timedelta(days=today.weekday())).isoformat()
        existing_w = db.get_journal_entry_by_date(monday, "weekly", manager_id=_mid())
        with st.form("journal_weekly"):
            reflection = st.text_area(
                "What went well this week? What would you do differently?",
                value=existing_w["content"] if existing_w else "",
                height=150,
            )
            st.markdown("**Self-Assessment** — Rate yourself this week:")
            scores = {}
            prev = db.get_latest_self_assessment(manager_id=_mid())
            for dim, question in templates.SELF_ASSESSMENT_DIMENSIONS:
                prev_val = prev.get(dim, 3)
                label = f"{dim} — {question}"
                if prev_val and prev_val != 3:
                    label += f" (last week: {prev_val})"
                scores[dim] = st.slider(label, 1, 5,
                    value=existing_w.get("mood", 3) if existing_w else 3,
                    key=f"sa_{dim}")
            submitted_w = st.form_submit_button("Save Weekly Reflection")

        if submitted_w:
            if existing_w:
                db.update_journal_entry(existing_w["id"], content=reflection)
            else:
                db.add_journal_entry(monday, "weekly", reflection, manager_id=_mid())
            db.save_self_assessment(monday, scores, manager_id=_mid())
            st.success(f"Weekly reflection saved for week of {monday}.")

    with tab_history:
        history = db.list_journal_entries(limit=30, manager_id=_mid())
        if not history:
            st.caption("No journal entries yet. Start writing — even one sentence counts.")
        else:
            # Export button
            export_df = pd.DataFrame(history)
            export_cols = [c for c in ["entry_date", "entry_type", "content", "mood",
                           "energy", "tags", "coaching_response"] if c in export_df.columns]
            st.download_button(
                "Export Journal (CSV)", export_df[export_cols].to_csv(index=False),
                "journal_export.csv", "text/csv", key="export_journal")
        for entry in history:
            mood_e = {1: "\U0001F62B", 2: "\U0001F615", 3: "\U0001F610",
                      4: "\U0001F642", 5: "\U0001F525"}.get(entry.get("mood"), "")
            energy_e = {1: "\U0001FAAB", 2: "\U0001F50B", 3: "\U0001F50B",
                        4: "\U0001F50B", 5: "\u26A1"}.get(entry.get("energy"), "")
            label = f"{entry['entry_date']} [{entry['entry_type']}] {mood_e}{energy_e}"
            with st.expander(label):
                if entry.get("content"):
                    st.markdown(entry["content"])
                if entry.get("coaching_response"):
                    st.markdown("---")
                    st.markdown("**Coaching Response:**")
                    st.markdown(entry["coaching_response"])
                if entry.get("private_notes"):
                    st.caption(f"Coaching notes: {entry['private_notes']}")
                if entry.get("tags"):
                    st.caption(f"Tags: {entry['tags']}")


def page_member_timeline():
    st.title("Member Timeline")
    show_toast()
    names, mapping = member_options()
    if not names:
        st.caption("Add team members first to see their timeline.")
        return

    selected = st.selectbox("Select team member", names)
    member_id = mapping[selected]

    # -- Pre-meeting prep panel --
    prep = db.get_pre_meeting_prep(member_id)
    if prep:
        st.subheader(f"Before your 1-on-1 with {selected}:")
        mc1, mc2, mc3, mc4 = st.columns(4)
        with mc1:
            days_m = prep.get("days_since_meeting")
            st.metric("Days since meeting", days_m if days_m is not None else "Never")
        with mc2:
            days_f = prep.get("days_since_feedback")
            st.metric("Days since feedback", days_f if days_f is not None else "Never")
        with mc3:
            st.metric("Pending actions", prep.get("pending_actions", 0))
        with mc4:
            st.metric("Active goals", len(prep.get("active_goals", [])))

        # Feedback ratio bar
        pos = prep.get("positive_count", 0)
        con = prep.get("constructive_count", 0)
        if pos + con > 0:
            ratio_pct = int(pos / (pos + con) * 100)
            st.progress(ratio_pct / 100,
                text=f"Feedback balance: {pos} positive / {con} constructive "
                     f"({ratio_pct}% positive, ideal >80%)")

        # Coaching provocations — the variable reward
        provocations = templates.get_coaching_provocations(prep)
        if provocations:
            st.markdown("**\U0001F4A1 Coaching Provocations:**")
            for p in provocations:
                st.warning(f"**{p['observation']}**")
                st.caption(f"> {p['wisdom']}")

    # -- Running 1:1 Notes (persistent across meetings) --
    recent_notes = db.list_running_notes(member_id, manager_id=_mid(), limit=5)
    if recent_notes:
        st.divider()
        st.subheader(f"Recent Notes about {selected}")
        for n in recent_notes:
            cat_icons = {"general": "\U0001F4DD", "meeting_prep": "\U0001F4C5",
                        "observation": "\U0001F440", "follow_up": "\U0001F504",
                        "praise": "\u2B50"}
            icon = cat_icons.get(n.get("category", ""), "\U0001F4DD")
            st.markdown(f"{icon} **{n['note_date']}** — {n['content']}")

    # -- Coaching pane for this member --
    st.divider()
    prep_left, prep_right = st.columns([3, 2])
    with prep_left:
        st.subheader("Quick Notes")
        meeting_notes = st.text_area(
            f"Notes about {selected} (meeting prep, observations, concerns):",
            height=120, placeholder="Type anything — then get coaching on it.",
            key=f"timeline_notes_{member_id}")
    with prep_right:
        render_coaching_pane(
            meeting_notes if meeting_notes else "",
            context_type="meeting_prep",
            member_name=selected,
            prep_data=prep,
            key_suffix=f"timeline_{member_id}")

    # -- Timeline feed --
    st.subheader("Activity Timeline")
    timeline = db.get_member_timeline(member_id)
    if not timeline:
        st.caption("No activity recorded for this member yet.")
    type_icons = {
        "event": "\U0001F4C5", "positive_feedback": "\U0001F4AC",
        "constructive_feedback": "\U0001F4AC", "goal": "\U0001F3AF",
        "action": "\u2705", "career": "\U0001F680",
    }
    for item in timeline:
        icon = type_icons.get(item["type"], "\U0001F4CB")
        label = f"{icon} {item['date']} — **{item['type'].replace('_', ' ').title()}**: {item.get('summary', '') or ''}"
        with st.expander(label):
            if item.get("detail"):
                st.markdown(item["detail"])


def page_analytics():
    st.title("Analytics & Insights")
    show_toast()

    # -- Anti-pattern detection (top of page — identity hook) --
    meeting_data = db.get_time_since_last_event_per_member(manager_id=_mid())
    feedback_data = db.get_feedback_ratios(manager_id=_mid())
    patterns = templates.detect_anti_patterns(meeting_data, feedback_data)

    if patterns:
        for p in patterns:
            st.error(f"**\u26A0\uFE0F {p['pattern']} Detected** — {p['evidence']}")
            st.markdown(f"*Suggestion:* {p['suggestion']}")
            st.caption(f"> {p['wisdom']}")
    else:
        st.success("\u2705 No anti-patterns detected. Your management behaviors are consistent.")

    # -- Management score --
    latest_sa = db.get_latest_self_assessment(manager_id=_mid())
    if latest_sa:
        avg_score = sum(latest_sa.values()) / len(latest_sa)
        st.metric("Management Score", f"{avg_score:.1f} / 5")

    # -- Tabs --
    tab_cadence, tab_feedback, tab_goals, tab_actions, tab_activity = st.tabs([
        "Meeting Cadence", "Feedback Health", "Goals", "Actions", "My Activity"
    ])

    with tab_cadence:
        data = db.get_meetings_per_member_per_month(manager_id=_mid())
        if data:
            df = pd.DataFrame(data)
            pivot = df.pivot_table(index="month", columns="member_name",
                                   values="meeting_count", fill_value=0)
            st.bar_chart(pivot)
            # Per-member staleness
            for m in meeting_data:
                days = m.get("days_since")
                name = m.get("member_name", "?")
                if days is None:
                    st.error(f"{name}: Never met")
                elif days > 14:
                    st.warning(f"{name}: {days} days since last meeting")
                else:
                    st.caption(f"{name}: {days} days since last meeting")
        else:
            st.caption("Complete some meetings to see cadence data.")

    with tab_feedback:
        if feedback_data:
            df = pd.DataFrame(feedback_data)
            st.bar_chart(df.set_index("member_name")[["positive_count", "constructive_count"]])
            st.caption("Ideal ratio: 5 positive for every 1 constructive (Losada & Heaphy)")
        else:
            st.caption("Record feedback to see health metrics.")

    with tab_goals:
        goal_data = db.get_goal_completion_rates(manager_id=_mid())
        if goal_data:
            st.dataframe(df_from(goal_data))
        else:
            st.caption("Set goals to track completion rates.")

    with tab_actions:
        stats = db.get_action_stats(manager_id=_mid())
        ac1, ac2, ac3, ac4 = st.columns(4)
        with ac1:
            st.metric("Total", stats.get("total", 0))
        with ac2:
            st.metric("Completed", stats.get("completed", 0))
        with ac3:
            st.metric("Pending", stats.get("pending", 0))
        with ac4:
            st.metric("Overdue", stats.get("overdue", 0))
        if stats.get("total", 0) > 0 and stats.get("completed"):
            rate = int(stats["completed"] / stats["total"] * 100)
            st.progress(rate / 100, text=f"Completion rate: {rate}%")

    with tab_activity:
        trends = db.get_manager_activity_trends(manager_id=_mid())
        if trends:
            df = pd.DataFrame(trends).set_index("week")
            st.line_chart(df)
        else:
            st.caption("Activity trends will appear after a few weeks of use.")

        # Self-assessment trends
        sa_trends = db.get_self_assessment_trends(manager_id=_mid())
        if sa_trends:
            sa_df = pd.DataFrame(sa_trends)
            pivot = sa_df.pivot_table(index="week_date", columns="dimension",
                                      values="score", fill_value=0)
            st.subheader("Self-Assessment Trends")
            st.line_chart(pivot)

    # -- Data Export --
    st.divider()
    st.subheader("Export Data")
    exp1, exp2, exp3 = st.columns(3)
    with exp1:
        events = db.list_events(status="completed", manager_id=_mid(), limit=500)
        if events:
            st.download_button("Export Meetings (CSV)",
                pd.DataFrame(events).to_csv(index=False),
                "meetings_export.csv", "text/csv", key="export_meetings")
    with exp2:
        feedback = db.list_feedback()
        if feedback:
            st.download_button("Export Feedback (CSV)",
                pd.DataFrame(feedback).to_csv(index=False),
                "feedback_export.csv", "text/csv", key="export_feedback")
    with exp3:
        all_goals = db.list_goals(manager_id=_mid())
        if all_goals:
            st.download_button("Export Goals (CSV)",
                pd.DataFrame(all_goals).to_csv(index=False),
                "goals_export.csv", "text/csv", key="export_goals")


def page_career_development():
    st.title("Career Development")
    show_toast()
    names, mapping = member_options()
    if not names:
        st.caption("Add team members first.")
        return

    selected = st.selectbox("Select team member", names, key="career_member")
    member_id = mapping[selected]

    tab_overview, tab_convos, tab_skills, tab_plans = st.tabs([
        "Overview", "Career Conversations", "Skills", "Development Plans"
    ])

    with tab_overview:
        skills = db.list_skills(member_id)
        plans = db.list_development_plans(member_id, status="active")
        convos = db.list_career_conversations(member_id, limit=1)
        strengths = [s["skill_name"] for s in skills if s.get("is_strength")]
        growth = [s["skill_name"] for s in skills if s.get("is_growth_area")]
        oc1, oc2, oc3 = st.columns(3)
        with oc1:
            st.metric("Skills tracked", len(skills))
            if strengths:
                st.markdown("**Strengths:** " + ", ".join(strengths))
        with oc2:
            st.metric("Active plans", len(plans))
            if growth:
                st.markdown("**Growth areas:** " + ", ".join(growth))
        with oc3:
            last_convo = convos[0]["conversation_date"] if convos else "Never"
            st.metric("Last career conversation", last_convo)

    with tab_convos:
        convos = db.list_career_conversations(member_id)
        for c in convos:
            with st.expander(f"{c['conversation_date']} — {c.get('topic', 'Career conversation')}"):
                if c.get("notes"):
                    st.markdown(c["notes"])
                if c.get("next_steps"):
                    st.markdown(f"**Next steps:** {c['next_steps']}")
        st.subheader("Log a Career Conversation")
        with st.form("add_career_convo"):
            cc1, cc2 = st.columns(2)
            with cc1:
                convo_date = st.date_input("Date", value=datetime.now().date())
                topic = st.text_input("Topic")
            with cc2:
                notes = st.text_area("Notes", height=100)
                next_steps = st.text_input("Next steps")
            if st.form_submit_button("Save Conversation"):
                db.add_career_conversation(member_id, convo_date.isoformat(), topic, notes, next_steps)
                set_toast("success", "Career conversation logged.")
                st.rerun()

    with tab_skills:
        skills = db.list_skills(member_id)
        if skills:
            st.dataframe(df_from(skills, ["skill_name", "proficiency", "is_strength", "is_growth_area"]))
        else:
            st.caption("No skills tracked yet for this member.")
        with st.form("add_skill"):
            sc1, sc2 = st.columns(2)
            with sc1:
                skill_name = st.text_input("Skill name")
                proficiency = st.selectbox("Proficiency",
                    ["learning", "developing", "proficient", "expert"])
            with sc2:
                is_str = st.checkbox("Strength")
                is_grow = st.checkbox("Growth area")
                skill_notes = st.text_input("Notes")
            if st.form_submit_button("Add Skill"):
                if skill_name:
                    db.add_skill(member_id, skill_name, proficiency, int(is_str), int(is_grow), skill_notes)
                    set_toast("success", f"Skill '{skill_name}' added.")
                    st.rerun()

    with tab_plans:
        plans = db.list_development_plans(member_id)
        for plan in plans:
            with st.expander(f"{plan['title']} [{plan['status']}]"):
                if plan.get("description"):
                    st.markdown(plan["description"])
                if plan.get("target_date"):
                    st.caption(f"Target: {plan['target_date']}")
                milestones = db.list_milestones(plan["id"])
                for ms in milestones:
                    done = "\u2705" if ms["completed"] else "\u2B1C"
                    st.markdown(f"{done} {ms['description']}")
                    if not ms["completed"]:
                        if st.button(f"Complete", key=f"ms_{ms['id']}"):
                            db.complete_milestone(ms["id"])
                            st.rerun()
                with st.form(f"add_ms_{plan['id']}"):
                    ms_desc = st.text_input("New milestone", key=f"msd_{plan['id']}")
                    if st.form_submit_button("Add"):
                        if ms_desc:
                            db.add_milestone(plan["id"], ms_desc)
                            st.rerun()
        st.subheader("Create Development Plan")
        with st.form("add_plan"):
            pc1, pc2 = st.columns(2)
            with pc1:
                plan_title = st.text_input("Plan title")
                plan_desc = st.text_area("Description", height=80)
            with pc2:
                plan_target = st.date_input("Target date")
            if st.form_submit_button("Create Plan"):
                if plan_title:
                    db.add_development_plan(member_id, plan_title, plan_desc, plan_target.isoformat())
                    set_toast("success", f"Plan '{plan_title}' created.")
                    st.rerun()


# ---------------------------------------------------------------------------
# Profile page
# ---------------------------------------------------------------------------

def page_my_profile():
    st.title("My Profile")
    show_toast()
    mid = get_current_manager_id()
    manager = db.get_manager(mid) if mid else None

    if not manager:
        st.warning("No profile found.")
        return

    st.subheader(f"Welcome, {manager['display_name']}")

    # Work schedule display
    try:
        schedule = json.loads(manager.get("work_schedule", "{}"))
    except (json.JSONDecodeError, TypeError):
        schedule = {}

    pc1, pc2 = st.columns(2)
    with pc1:
        st.markdown(f"**Username:** {manager['username']}")
        st.markdown(f"**Email:** {manager.get('email') or 'Not set'}")
        st.markdown(f"**Timezone:** {manager.get('timezone', 'Not set')}")
    with pc2:
        days = schedule.get("days", [])
        st.markdown(f"**Work days:** {', '.join(days) if days else 'Not set'}")
        st.markdown(f"**Hours:** {schedule.get('start', '?')} — {schedule.get('end', '?')}")

    st.divider()
    st.subheader("Update Profile")
    with st.form("update_profile"):
        uc1, uc2 = st.columns(2)
        with uc1:
            new_name = st.text_input("Display name", value=manager["display_name"])
            new_email = st.text_input("Email", value=manager.get("email") or "")
            new_tz = st.text_input("Timezone", value=manager.get("timezone", "America/New_York"))
        with uc2:
            days_options = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            new_days = st.multiselect("Work days", days_options,
                default=schedule.get("days", ["Mon", "Tue", "Wed", "Thu", "Fri"]))
            new_start = st.time_input("Work start",
                value=datetime.strptime(schedule.get("start", "09:00"), "%H:%M").time())
            new_end = st.time_input("Work end",
                value=datetime.strptime(schedule.get("end", "17:00"), "%H:%M").time())
        if st.form_submit_button("Save Changes"):
            new_schedule = json.dumps({
                "days": new_days,
                "start": new_start.strftime("%H:%M"),
                "end": new_end.strftime("%H:%M"),
            })
            db.update_manager(mid, display_name=new_name,
                email=new_email or None, work_schedule=new_schedule,
                timezone=new_tz)
            st.session_state["manager_name"] = new_name
            set_toast("success", "Profile updated.")
            st.rerun()

    with st.expander("Change Password"):
        with st.form("change_pw"):
            new_pw = st.text_input("New password", type="password")
            new_pw2 = st.text_input("Confirm new password", type="password")
            if st.form_submit_button("Update Password"):
                if not new_pw:
                    st.warning("Enter a new password.")
                elif len(new_pw) < 8:
                    st.error("Password must be at least 8 characters.")
                elif new_pw != new_pw2:
                    st.error("Passwords don't match.")
                else:
                    db.update_manager_password(mid, new_pw)
                    st.success("Password updated.")


# ---------------------------------------------------------------------------
# Delegation Tracker
# ---------------------------------------------------------------------------

def page_delegations():
    st.title("Delegation Tracker")
    show_toast()
    st.caption(
        '*"Delegate results, not methods. Prescribing methods removes '
        'accountability."* — Dellanna'
    )

    names, name_map = member_options()

    # -- Active delegations --
    active = db.list_delegations(manager_id=_mid(), status="active")
    overdue = db.get_overdue_delegations(manager_id=_mid())

    if overdue:
        st.warning(f"{len(overdue)} delegation(s) past check-in date!")

    if active:
        st.subheader(f"Active Delegations ({len(active)})")
        for d in active:
            is_overdue = d in overdue
            icon = "\U0001F534" if is_overdue else "\U0001F7E2"
            label = (f"{icon} {d['task'][:60]} — "
                     f"{d.get('member_name', 'Unassigned')} "
                     f"[{d['autonomy_level']}]")
            with st.expander(label):
                st.markdown(f"**Expected Outcome:** {d.get('outcome_expected', 'N/A')}")
                st.markdown(f"**Autonomy Level:** {d['autonomy_level'].title()} "
                           f"&nbsp; **Check-in:** {d.get('check_in_date', 'Not set')}")
                if d.get("notes"):
                    st.markdown(f"**Notes:** {d['notes']}")
                dc1, dc2, dc3 = st.columns(3)
                with dc1:
                    if st.button("Complete", key=f"del_done_{d['id']}"):
                        db.update_delegation(d["id"], status="completed")
                        set_toast("success", f"Delegation '{d['task'][:30]}' completed.")
                        st.rerun()
                with dc2:
                    if st.button("Stalled", key=f"del_stall_{d['id']}"):
                        db.update_delegation(d["id"], status="stalled")
                        set_toast("warning", f"Delegation marked as stalled.")
                        st.rerun()
                with dc3:
                    if st.button("Delete", key=f"del_rm_{d['id']}"):
                        db.delete_delegation(d["id"])
                        set_toast("success", "Delegation deleted.")
                        st.rerun()
    else:
        st.info("No active delegations. Use the form below to delegate a task.")

    # -- Add delegation form --
    st.subheader("Delegate a Task")
    with st.form("add_delegation"):
        task = st.text_input("What are you delegating? *")
        member = st.selectbox("Delegated to", ["(none)"] + names)
        outcome = st.text_area("Expected outcome (results, not methods)", height=80)
        dc1, dc2 = st.columns(2)
        with dc1:
            autonomy = st.selectbox("Autonomy level", [
                ("directed", "Directed — step-by-step guidance"),
                ("guided", "Guided — check in at milestones"),
                ("autonomous", "Autonomous — deliver the result"),
            ], format_func=lambda x: x[1])
        with dc2:
            check_in = st.date_input("Check-in date", value=None)
        notes = st.text_input("Notes (optional)")
        if st.form_submit_button("Delegate", use_container_width=True):
            if not task:
                st.error("Describe what you're delegating.")
            else:
                member_id = name_map.get(member) if member != "(none)" else None
                db.add_delegation(
                    task=task, team_member_id=member_id,
                    outcome_expected=outcome or None,
                    autonomy_level=autonomy[0],
                    check_in_date=check_in.isoformat() if check_in else None,
                    notes=notes or None, manager_id=_mid(),
                )
                set_toast("success", f"Delegated: {task[:40]}")
                st.rerun()

    # -- History --
    with st.expander("Completed / Past Delegations"):
        past = db.list_delegations(manager_id=_mid())
        past = [d for d in past if d["status"] != "active"]
        if past:
            st.dataframe(
                df_from(past, ["id", "task", "member_name", "autonomy_level",
                               "status", "created_at", "completed_at"]),
                use_container_width=True, hide_index=True,
            )
        else:
            st.caption("No completed delegations yet.")


# ---------------------------------------------------------------------------
# Running 1:1 Notes
# ---------------------------------------------------------------------------

def page_running_notes():
    st.title("Running 1:1 Notes")
    show_toast()
    st.caption(
        "Persistent notes per team member — visible at every meeting prep. "
        "Not tied to a single event."
    )

    names, mapping = member_options()
    if not names:
        st.info("Add team members first.")
        return

    selected = st.selectbox("Team member", names, key="rn_member")
    member_id = mapping[selected]

    # -- Add note --
    with st.form("add_running_note"):
        nc1, nc2 = st.columns([3, 1])
        with nc1:
            content = st.text_area(f"Note about {selected}", height=100,
                placeholder="Observation, meeting prep, follow-up, praise...")
        with nc2:
            category = st.selectbox("Category", [
                "general", "meeting_prep", "observation", "follow_up", "praise"])
            note_date = st.date_input("Date", value=datetime.now().date())
        if st.form_submit_button("Add Note", use_container_width=True):
            if content and content.strip():
                db.add_running_note(
                    team_member_id=member_id, content=content,
                    category=category, note_date=note_date.isoformat(),
                    manager_id=_mid(),
                )
                set_toast("success", "Note added.")
                st.rerun()
            else:
                st.warning("Write something first.")

    # -- Display notes --
    notes = db.list_running_notes(member_id, manager_id=_mid())
    if notes:
        st.subheader(f"Notes for {selected} ({len(notes)})")
        category_icons = {
            "general": "\U0001F4DD", "meeting_prep": "\U0001F4C5",
            "observation": "\U0001F440", "follow_up": "\U0001F504",
            "praise": "\u2B50",
        }
        for n in notes:
            icon = category_icons.get(n.get("category", ""), "\U0001F4DD")
            col_note, col_del = st.columns([6, 1])
            with col_note:
                st.markdown(
                    f"{icon} **{n['note_date']}** [{n.get('category', 'general')}]  \n"
                    f"{n['content']}"
                )
            with col_del:
                if st.button("X", key=f"del_rn_{n['id']}"):
                    db.delete_running_note(n["id"])
                    st.rerun()
            st.markdown("---")
    else:
        st.caption(f"No notes yet for {selected}. Start building a history.")


# ---------------------------------------------------------------------------
# Decision Log
# ---------------------------------------------------------------------------

def page_decision_log():
    st.title("Decision Log")
    show_toast()
    st.caption(
        '*"For every unambiguous decision we make, we probably nudge things '
        'a dozen times."* — Andy Grove'
    )

    # -- Decisions due for review (nudge) --
    due = db.get_decisions_due_for_review(manager_id=_mid())
    if due:
        st.warning(f"{len(due)} decision(s) due for review:")
        for d in due:
            st.markdown(f"- **{d['title']}** (review date: {d['review_date']})")

    # -- Add decision form --
    st.subheader("Record a Decision")
    with st.form("add_decision"):
        title = st.text_input("Decision *", placeholder="What did you decide?")
        context = st.text_area("Context", height=80,
            placeholder="What situation or problem prompted this decision?")
        alternatives = st.text_area("Alternatives considered", height=60,
            placeholder="What other options did you weigh?")
        rationale = st.text_area("Rationale", height=80,
            placeholder="Why this option? What trade-offs did you accept?")
        dc1, dc2 = st.columns(2)
        with dc1:
            expected = st.text_input("Expected outcome",
                placeholder="What should happen if this is the right call?")
        with dc2:
            review_date = st.date_input("Review by (when to check if it worked)",
                                         value=None)
        if st.form_submit_button("Log Decision", use_container_width=True):
            if not title:
                st.error("Describe the decision.")
            else:
                db.add_decision(
                    title=title, context=context or None,
                    alternatives=alternatives or None,
                    rationale=rationale or None,
                    expected_outcome=expected or None,
                    review_date=review_date.isoformat() if review_date else None,
                    manager_id=_mid(),
                )
                set_toast("success", f"Decision logged: {title[:40]}")
                st.rerun()

    # -- Decision history --
    st.subheader("Decision History")
    decisions = db.list_decisions(manager_id=_mid())
    if not decisions:
        st.caption("No decisions logged yet. Start recording your thinking.")
        return

    statuses = ["active", "validated", "revised", "reversed"]
    status_colors = {"active": "blue", "validated": "green",
                     "revised": "orange", "reversed": "red"}

    for d in decisions:
        color = status_colors.get(d["status"], "blue")
        label = (f":{color}[**{d['status'].upper()}**] "
                 f"{d['title']} — {d['created_at'][:10]}")
        with st.expander(label):
            if d.get("context"):
                st.markdown(f"**Context:** {d['context']}")
            if d.get("alternatives"):
                st.markdown(f"**Alternatives:** {d['alternatives']}")
            if d.get("rationale"):
                st.markdown(f"**Rationale:** {d['rationale']}")
            if d.get("expected_outcome"):
                st.markdown(f"**Expected outcome:** {d['expected_outcome']}")
            if d.get("review_date"):
                st.markdown(f"**Review by:** {d['review_date']}")
            if d.get("actual_outcome"):
                st.markdown(f"**Actual outcome:** {d['actual_outcome']}")

            # Update status / record actual outcome
            with st.form(f"update_decision_{d['id']}"):
                uc1, uc2 = st.columns(2)
                with uc1:
                    new_status = st.selectbox("Update status", statuses,
                        index=statuses.index(d["status"]),
                        key=f"ds_{d['id']}")
                with uc2:
                    actual = st.text_input("Actual outcome (what really happened)",
                        value=d.get("actual_outcome") or "",
                        key=f"da_{d['id']}")
                uf1, uf2 = st.columns(2)
                with uf1:
                    if st.form_submit_button("Update", use_container_width=True):
                        updates = {"status": new_status}
                        if actual:
                            updates["actual_outcome"] = actual
                        db.update_decision(d["id"], **updates)
                        set_toast("success", f"Decision updated.")
                        st.rerun()
            if st.button("Delete", key=f"del_dec_{d['id']}"):
                db.delete_decision(d["id"])
                set_toast("success", "Decision deleted.")
                st.rerun()


# ---------------------------------------------------------------------------
# Sidebar navigation & dispatch
# ---------------------------------------------------------------------------

_DISPATCH = {
    "Dashboard": page_dashboard,
    "Journal": page_journal,
    "Team": page_team_roster,
    "Timeline": page_member_timeline,
    "1:1 Notes": page_running_notes,
    "Career Dev": page_career_development,
    "Actions": page_action_items,
    "Feedback": page_record_feedback,
    "Delegations": page_delegations,
    "Goals": page_quarterly_goals,
    "Decisions": page_decision_log,
    "Schedule": page_schedule_event,
    "Upcoming": page_upcoming_events,
    "History": page_event_history,
    "Analytics": page_analytics,
    "Resources": page_resources,
    "Settings": page_configuration,
    "My Profile": page_my_profile,
    # Legacy routes (for backward compat with session state)
    "Team Roster": page_team_roster,
    "Member Timeline": page_member_timeline,
    "Running Notes": page_running_notes,
    "Career Development": page_career_development,
    "Action Items": page_action_items,
    "Record Feedback": page_record_feedback,
    "Quarterly Goals": page_quarterly_goals,
    "Decision Log": page_decision_log,
    "Schedule Event": page_schedule_event,
    "Upcoming Events": page_upcoming_events,
    "Event History": page_event_history,
    "Configuration": page_configuration,
    "Add Member": page_team_roster,
    "Add Action": page_action_items,
    "Add Goal": page_quarterly_goals,
    "Agenda Templates": page_resources,
    "Management Tips": page_resources,
}


def _nav_button(label, page_key, current_page):
    """Render a compact nav button. Returns True if clicked."""
    btn_type = "primary" if current_page == page_key else "secondary"
    if st.button(label, key=f"nav_{page_key}",
                 use_container_width=True, type=btn_type):
        st.session_state["nav_page"] = page_key
        st.rerun()


def main():
    # Auth gate
    if not require_auth():
        return

    manager_name = st.session_state.get("manager_name", "Manager")
    current_page = st.session_state.get("nav_page", "Dashboard")

    # -- Sidebar: flat, compact, workflow-ordered --
    with st.sidebar:
        st.markdown("### \U0001F4CB Manager Tool")

        # Streak + daily status (engagement hook)
        streak = db.get_journal_streak(manager_id=_mid())
        today_entry = db.get_journal_entry_by_date(
            datetime.now().date().isoformat(), "daily", manager_id=_mid())
        streak_text = f"\U0001F525 **{streak}-day streak**" if streak > 0 else ""
        journal_dot = "\U0001F7E2" if today_entry else "\U0001F534"
        st.markdown(f"{streak_text} &nbsp; {journal_dot} *{manager_name}*")

        st.markdown("---")

        # -- Primary actions (daily use) --
        _nav_button("\U0001F4CA  Dashboard", "Dashboard", current_page)
        _nav_button("\u270D\uFE0F  Journal", "Journal", current_page)

        st.caption("PEOPLE")
        _nav_button("\U0001F465  Team", "Team", current_page)
        _nav_button("\U0001F550  Timeline", "Timeline", current_page)
        _nav_button("\U0001F4DD  1:1 Notes", "1:1 Notes", current_page)
        _nav_button("\U0001F680  Career Dev", "Career Dev", current_page)

        st.caption("TRACKING")
        # Badge counts for urgency
        _summary = db.get_weekly_summary(manager_id=_mid()) or {}
        overdue_actions = len(_summary.get("overdue_actions", []))
        actions_label = f"\u2705  Actions ({overdue_actions} overdue)" if overdue_actions else "\u2705  Actions"
        _nav_button(actions_label, "Actions", current_page)
        _nav_button("\U0001F4AC  Feedback", "Feedback", current_page)
        _nav_button("\U0001F4E4  Delegations", "Delegations", current_page)
        _nav_button("\U0001F3AF  Goals", "Goals", current_page)
        _nav_button("\U0001F9E0  Decisions", "Decisions", current_page)

        st.caption("EVENTS")
        _nav_button("\U0001F4C5  Schedule", "Schedule", current_page)
        _nav_button("\U0001F4C6  Upcoming", "Upcoming", current_page)
        _nav_button("\U0001F4DA  History", "History", current_page)

        st.caption("REFERENCE")
        _nav_button("\U0001F4C8  Analytics", "Analytics", current_page)
        _nav_button("\U0001F4DA  Resources", "Resources", current_page)

        st.markdown("---")
        _nav_button("\u2699\uFE0F  Settings", "Settings", current_page)
        if st.button("\U0001F6AA  Log Out", use_container_width=True):
            for key in ["manager_id", "manager_name", "nav_page"]:
                st.session_state.pop(key, None)
            st.rerun()

    # -- Dispatch --
    handler = _DISPATCH.get(current_page, page_dashboard)
    handler()


main()

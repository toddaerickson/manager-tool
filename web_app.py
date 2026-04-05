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

import database as db
import templates
import auth

# ---------------------------------------------------------------------------
# App config
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Manager Tool", page_icon="\U0001F4CB", layout="wide")
db.init_db()

# Ensure a default page is set
if "page" not in st.session_state:
    st.session_state["page"] = "Dashboard"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def navigate(page_name):
    """Set the active page in session state."""
    st.session_state["page"] = page_name


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
    members = db.list_team_members()
    mapping = {m["name"]: m["id"] for m in members}
    return list(mapping.keys()), mapping


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
    st.title("Weekly Dashboard")

    summary = db.get_weekly_summary()

    # -- Metrics row --
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Upcoming", len(summary["upcoming_events"]))
    c2.metric("Completed", len(summary["completed_events"]))
    c3.metric("Pending Actions", len(summary["pending_actions"]))
    c4.metric("Overdue", len(summary["overdue_actions"]))

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

    st.divider()
    st.info(f"**Tip of the Week:** {templates.get_random_tip()}")


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
        )
        st.toast(f"Event #{eid} scheduled: {final_title}", icon="\U0001F4C5")
        st.rerun()


def page_upcoming_events():
    st.title("Upcoming Events (Next 14 Days)")

    events = db.get_upcoming_events(days=14)
    if not events:
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

    members = db.list_team_members()
    if not members:
        st.info("No team members yet. Use **Add Member** to add someone.")
        return

    # Search (#4)
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
            st.markdown(
                f":{color}[**{fb['feedback_type'].upper()}**] "
                f"— {fb['created_at'][:10]}  \n"
                f"&nbsp;&nbsp;**S:** {fb.get('situation', 'N/A')}  \n"
                f"&nbsp;&nbsp;**B:** {fb.get('behavior', 'N/A')}  \n"
                f"&nbsp;&nbsp;**I:** {fb.get('impact', 'N/A')}"
            )


def page_add_member():
    st.title("Add Team Member")

    # Single-column form (#7)
    with st.form("add_member"):
        name = st.text_input("Full Name *")
        email = st.text_input("Email")
        role = st.text_input("Role / Title")
        start_date = st.date_input("Start Date", value=datetime.now().date())
        notes = st.text_input("Notes")
        submitted = st.form_submit_button("Add Member", use_container_width=True)

    if submitted:
        if not name:
            st.error("Name is required.")
        else:
            mid = db.add_team_member(
                name, email or None, role or None,
                start_date.strftime("%Y-%m-%d"), notes or None,
            )
            st.toast(f"Added {name} (ID: {mid})", icon="\u2705")
            st.rerun()


# -- Tracking ---------------------------------------------------------------

def page_action_items():
    st.title("Pending Action Items")

    actions = db.get_pending_action_items()
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


def page_add_action():
    st.title("Add Action Item")

    # Single-column form (#7)
    with st.form("add_action"):
        desc = st.text_input("Description *")
        assignee = st.text_input("Assignee")
        due_date = st.date_input("Due Date", value=None)
        event_id = st.text_input("Related Event ID (optional)")
        submitted = st.form_submit_button("Add Action Item",
                                          use_container_width=True)

    if submitted:
        if not desc:
            st.error("Description is required.")
        else:
            eid = int(event_id) if event_id and event_id.isdigit() else None
            due = due_date.strftime("%Y-%m-%d") if due_date else None
            aid = db.add_action_item(desc, event_id=eid,
                                     assignee=assignee or None, due_date=due)
            st.toast(f"Action item #{aid} added.", icon="\u2705")
            st.rerun()


def page_record_feedback():
    st.title("Record Feedback (SBI Framework)")

    names, name_map = member_options()
    if not names:
        st.warning("No team members yet. Add one first.")
        return

    st.info(
        "**SBI Framework**  \n"
        "**Situation:** When/where did this happen?  \n"
        "**Behavior:** What specifically did they do?  \n"
        "**Impact:** What was the result/effect?"
    )

    # Single-column form (#7)
    with st.form("feedback_form"):
        member_name = st.selectbox("Team Member", names)
        fb_type = st.radio("Feedback Type", ["Positive", "Constructive"],
                           horizontal=True)
        situation = st.text_input("Situation")
        behavior = st.text_input("Behavior")
        impact = st.text_input("Impact")
        submitted = st.form_submit_button("Save Feedback",
                                          use_container_width=True)

    if submitted:
        mid = name_map.get(member_name)
        if not mid:
            st.error("Select a team member.")
        else:
            fb = "positive" if fb_type == "Positive" else "constructive"
            fid = db.add_feedback(mid, fb, situation or None,
                                  behavior or None, impact or None)
            st.toast(f"Feedback #{fid} recorded.", icon="\u2705")
            st.rerun()


# -- Goals ------------------------------------------------------------------

def page_quarterly_goals():
    st.title("Quarterly Goals")

    goals = db.list_goals()
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
    with st.form("update_goal"):
        gid = st.number_input("Goal ID to update", min_value=1, step=1)
        new_status = st.selectbox("New Status", statuses)
        if st.form_submit_button("Update Status", use_container_width=True):
            db.update_goal(int(gid), status=new_status)
            st.toast(f"Goal #{gid} updated to '{new_status}'.", icon="\u2705")
            st.rerun()


def page_add_goal():
    st.title("Add Quarterly Goal")

    names, name_map = member_options()
    if not names:
        st.warning("No team members yet. Add one first.")
        return

    now = datetime.now()
    q = (now.month - 1) // 3 + 1
    default_quarter = f"Q{q} {now.year}"

    # Single-column form (#7)
    with st.form("add_goal"):
        member_name = st.selectbox("Team Member", names)
        quarter = st.text_input("Quarter", value=default_quarter)
        description = st.text_input("Goal Description *")
        key_results = st.text_area("Key Results (one per line, optional)")
        submitted = st.form_submit_button("Add Goal", use_container_width=True)

    if submitted:
        mid = name_map.get(member_name)
        if not mid:
            st.error("Select a team member.")
        elif not description:
            st.error("Description is required.")
        else:
            gid = db.add_goal(mid, quarter, description, key_results or None)
            st.toast(f"Goal #{gid} added for {member_name}.", icon="\u2705")
            st.rerun()


# -- Resources --------------------------------------------------------------

def page_agenda_templates():
    st.title("Meeting Agenda Templates")

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


def page_management_tips():
    st.title("Management Tips")

    tips = templates.get_tips_by_count(8)
    for i, tip in enumerate(tips, 1):
        st.markdown(f"**{i}.** {tip}")

    st.divider()
    st.subheader("Common Anti-Patterns")
    for ap in templates.ANTI_PATTERNS:
        with st.container():
            st.markdown(f":red[**{ap['name']}**]")
            st.markdown(f"*Symptom:* {ap['symptom']}")
            st.markdown(f":green[*Fix:* {ap['fix']}]")
            st.markdown("---")

    with st.expander("Weekly Manager Self-Assessment"):
        for dim, question in templates.SELF_ASSESSMENT_DIMENSIONS:
            st.markdown(f"**{dim}** — {question} &nbsp; ___/5")


# -- Settings ---------------------------------------------------------------

def page_configuration():
    st.title("Email & Profile Configuration")

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


# ---------------------------------------------------------------------------
# Sidebar navigation with grouped expanders (#1) & dispatch
# ---------------------------------------------------------------------------

NAV_GROUPS = [
    ("Overview", {
        "\U0001F4CA  Dashboard": "Dashboard",
    }),
    ("Activities", {
        "\U0001F4C5  Schedule Event": "Schedule Event",
        "\U0001F4C6  Upcoming Events": "Upcoming Events",
        "\U0001F4D6  Event History": "Event History",
    }),
    ("People", {
        "\U0001F465  Team Roster": "Team Roster",
        "\U0001F464  Add Member": "Add Member",
    }),
    ("Tracking", {
        "\u2705  Action Items": "Action Items",
        "\u2795  Add Action": "Add Action",
        "\U0001F4AC  Record Feedback": "Record Feedback",
    }),
    ("Goals", {
        "\U0001F3AF  Quarterly Goals": "Quarterly Goals",
        "\U0001F4DD  Add Goal": "Add Goal",
    }),
    ("Resources", {
        "\U0001F4CB  Agenda Templates": "Agenda Templates",
        "\U0001F4A1  Management Tips": "Management Tips",
    }),
    ("Settings", {
        "\u2699\uFE0F  Configuration": "Configuration",
    }),
]

DISPATCH = {
    "Dashboard": page_dashboard,
    "Schedule Event": page_schedule_event,
    "Upcoming Events": page_upcoming_events,
    "Event History": page_event_history,
    "Team Roster": page_team_roster,
    "Add Member": page_add_member,
    "Action Items": page_action_items,
    "Add Action": page_add_action,
    "Record Feedback": page_record_feedback,
    "Quarterly Goals": page_quarterly_goals,
    "Add Goal": page_add_goal,
    "Agenda Templates": page_agenda_templates,
    "Management Tips": page_management_tips,
    "Configuration": page_configuration,
}


def main():
    # ── Authentication gate ──
    if not auth.require_auth():
        return

    # ── Sidebar ──
    with st.sidebar:
        st.title("Manager Tool")

        # User info
        user = auth.get_current_user()
        if user:
            col_pic, col_name = st.columns([1, 3])
            with col_pic:
                if user.get("picture"):
                    st.image(user["picture"], width=40)
            with col_name:
                st.markdown(f"**{user.get('name', '')}**")
                st.caption(user.get("email", ""))
            if st.button("Sign out", use_container_width=True):
                auth.logout()
                st.rerun()
            st.divider()

        # Grouped navigation (#1)
        current = st.session_state.get("page", "Dashboard")
        for group_label, items in NAV_GROUPS:
            # Overview group has no expander — always visible
            if group_label == "Overview":
                for btn_label, page_key in items.items():
                    btype = "primary" if current == page_key else "secondary"
                    if st.button(btn_label, key=f"nav_{page_key}",
                                 use_container_width=True, type=btype):
                        navigate(page_key)
                        st.rerun()
            else:
                group_active = current in items.values()
                with st.expander(f"**{group_label}**", expanded=group_active):
                    for btn_label, page_key in items.items():
                        btype = "primary" if current == page_key else "secondary"
                        if st.button(btn_label, key=f"nav_{page_key}",
                                     use_container_width=True, type=btype):
                            navigate(page_key)
                            st.rerun()

    # ── Page dispatch ──
    handler = DISPATCH.get(st.session_state.get("page", "Dashboard"),
                           page_dashboard)
    handler()


main()

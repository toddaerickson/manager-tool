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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
# Pages
# ---------------------------------------------------------------------------

def page_dashboard():
    st.title("Weekly Dashboard")
    show_toast()

    summary = db.get_weekly_summary()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Upcoming", len(summary["upcoming_events"]))
    c2.metric("Completed", len(summary["completed_events"]))
    c3.metric("Pending Actions", len(summary["pending_actions"]))
    c4.metric("Overdue", len(summary["overdue_actions"]))

    if summary["upcoming_events"]:
        st.subheader("Upcoming Events This Week")
        st.dataframe(
            df_from(summary["upcoming_events"],
                    ["id", "title", "event_type", "scheduled_date", "scheduled_time", "participant_name"]),
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
    show_toast()

    type_keys = list(templates.EVENT_TYPES.keys())
    type_labels = [templates.EVENT_TYPES[t]["label"] for t in type_keys]
    names, name_map = member_options()

    with st.form("schedule_form"):
        col1, col2 = st.columns(2)
        with col1:
            event_label = st.selectbox("Event Type", type_labels)
            title = st.text_input("Title (leave blank for default)")
            date = st.date_input("Date", value=datetime.now().date())
            time_val = st.time_input("Time", value=datetime.strptime("10:00", "%H:%M").time())
        with col2:
            participant = st.selectbox("Participant", ["(none)"] + names)
            duration = st.number_input("Duration (min)", value=30, min_value=5, step=5)
            location = st.text_input("Location / meeting link")
            gen_agenda = st.checkbox("Generate agenda from template")

        submitted = st.form_submit_button("Schedule Event")

    if submitted:
        idx = type_labels.index(event_label)
        event_type = type_keys[idx]
        member_name = participant if participant != "(none)" else None
        member_id = name_map.get(participant) if member_name else None
        final_title = title or templates.get_default_title(event_type, member_name)
        agenda = templates.generate_agenda(event_type, member_name) if gen_agenda else None

        eid = db.create_event(
            title=final_title, event_type=event_type,
            scheduled_date=date.strftime("%Y-%m-%d"),
            scheduled_time=time_val.strftime("%H:%M"),
            team_member_id=member_id,
            duration_minutes=duration,
            location=location or None,
            agenda=agenda,
        )
        set_toast("success", f"Event #{eid} scheduled: {final_title}")
        st.rerun()


def page_upcoming_events():
    st.title("Upcoming Events (Next 14 Days)")
    show_toast()

    events = db.get_upcoming_events(days=14)
    if events:
        st.dataframe(
            df_from(events, ["id", "title", "event_type", "scheduled_date",
                             "scheduled_time", "participant_name", "status"]),
            use_container_width=True, hide_index=True,
        )
        with st.form("complete_event"):
            eid = st.number_input("Event ID to complete", min_value=1, step=1)
            notes = st.text_area("Meeting notes (optional)")
            if st.form_submit_button("Mark Complete"):
                event = db.get_event(int(eid))
                if event:
                    db.complete_event(int(eid), notes=notes or None)
                    set_toast("success", f"Event #{eid} marked completed.")
                else:
                    set_toast("error", f"Event #{eid} not found.")
                st.rerun()
    else:
        st.info("No upcoming events scheduled.")


def page_event_history():
    st.title("Event History")
    events = db.list_events(status="completed", limit=30)
    if events:
        st.dataframe(
            df_from(events, ["id", "title", "event_type", "scheduled_date",
                             "participant_name", "status"]),
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("No completed events yet.")


# -- People -----------------------------------------------------------------

def page_team_roster():
    st.title("Team Roster")
    show_toast()

    members = db.list_team_members()
    if members:
        st.dataframe(
            df_from(members, ["id", "name", "email", "role", "start_date"]),
            use_container_width=True, hide_index=True,
        )
        mid = st.selectbox("Select a member for details",
                           options=[m["id"] for m in members],
                           format_func=lambda x: next(
                               (f"{m['name']} (ID {m['id']})" for m in members if m["id"] == x), str(x)))

        if st.button("View Details"):
            st.session_state["detail_member_id"] = mid

        if "detail_member_id" in st.session_state:
            summary = db.get_member_summary(st.session_state["detail_member_id"])
            if summary:
                _render_member_detail(summary)
    else:
        st.info("No team members yet. Use **Add Member** to add someone.")


def _render_member_detail(summary):
    m = summary["member"]
    st.divider()
    st.subheader(f"Member: {m['name']}")
    col1, col2, col3 = st.columns(3)
    col1.markdown(f"**Role:** {m.get('role', 'N/A')}")
    col2.markdown(f"**Email:** {m.get('email', 'N/A')}")
    col3.markdown(f"**Start:** {m.get('start_date', 'N/A')}")
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
                f":{color}[**{fb['feedback_type'].upper()}**] — {fb['created_at'][:10]}  \n"
                f"&nbsp;&nbsp;**S:** {fb.get('situation', 'N/A')}  \n"
                f"&nbsp;&nbsp;**B:** {fb.get('behavior', 'N/A')}  \n"
                f"&nbsp;&nbsp;**I:** {fb.get('impact', 'N/A')}"
            )


def page_add_member():
    st.title("Add Team Member")
    show_toast()

    with st.form("add_member"):
        name = st.text_input("Full Name *")
        col1, col2 = st.columns(2)
        with col1:
            email = st.text_input("Email")
            role = st.text_input("Role / Title")
        with col2:
            start_date = st.date_input("Start Date", value=datetime.now().date())
            notes = st.text_input("Notes")
        submitted = st.form_submit_button("Add Member")

    if submitted:
        if not name:
            st.error("Name is required.")
        else:
            mid = db.add_team_member(
                name, email or None, role or None,
                start_date.strftime("%Y-%m-%d"), notes or None,
            )
            set_toast("success", f"Added {name} (ID: {mid})")
            st.rerun()


# -- Tracking ---------------------------------------------------------------

def page_action_items():
    st.title("Pending Action Items")
    show_toast()

    actions = db.get_pending_action_items()
    if actions:
        st.dataframe(
            df_from(actions, ["id", "description", "assignee", "due_date", "status", "event_title"]),
            use_container_width=True, hide_index=True,
        )
        today = datetime.now().strftime("%Y-%m-%d")
        overdue = [a for a in actions if a.get("due_date") and a["due_date"] < today]
        if overdue:
            st.warning(f"{len(overdue)} overdue action item(s)!")

        with st.form("complete_action"):
            aid = st.number_input("Action ID to complete", min_value=1, step=1)
            if st.form_submit_button("Mark Complete"):
                db.complete_action_item(int(aid))
                set_toast("success", f"Action item #{aid} completed.")
                st.rerun()
    else:
        st.success("No pending action items. You're caught up!")


def page_add_action():
    st.title("Add Action Item")
    show_toast()

    with st.form("add_action"):
        desc = st.text_input("Description *")
        col1, col2 = st.columns(2)
        with col1:
            assignee = st.text_input("Assignee")
            due_date = st.date_input("Due Date", value=None)
        with col2:
            event_id = st.text_input("Related Event ID (optional)")
        submitted = st.form_submit_button("Add Action Item")

    if submitted:
        if not desc:
            st.error("Description is required.")
        else:
            eid = int(event_id) if event_id and event_id.isdigit() else None
            due = due_date.strftime("%Y-%m-%d") if due_date else None
            aid = db.add_action_item(desc, event_id=eid, assignee=assignee or None, due_date=due)
            set_toast("success", f"Action item #{aid} added.")
            st.rerun()


def page_record_feedback():
    st.title("Record Feedback (SBI Framework)")
    show_toast()

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


# -- Goals ------------------------------------------------------------------

def page_quarterly_goals():
    st.title("Quarterly Goals")
    show_toast()

    goals = db.list_goals()
    if goals:
        st.dataframe(
            df_from(goals, ["id", "member_name", "quarter", "description", "status"]),
            use_container_width=True, hide_index=True,
        )
        statuses = ["not_started", "in_progress", "met", "exceeded", "partially_met", "not_met"]
        with st.form("update_goal"):
            col1, col2 = st.columns(2)
            with col1:
                gid = st.number_input("Goal ID to update", min_value=1, step=1)
            with col2:
                new_status = st.selectbox("New Status", statuses)
            if st.form_submit_button("Update Status"):
                db.update_goal(int(gid), status=new_status)
                set_toast("success", f"Goal #{gid} updated to '{new_status}'.")
                st.rerun()
    else:
        st.info("No goals set yet.")


def page_add_goal():
    st.title("Add Quarterly Goal")
    show_toast()

    names, name_map = member_options()
    if not names:
        st.warning("No team members yet. Add one first.")
        return

    now = datetime.now()
    q = (now.month - 1) // 3 + 1
    default_quarter = f"Q{q} {now.year}"

    with st.form("add_goal"):
        member_name = st.selectbox("Team Member", names)
        quarter = st.text_input("Quarter", value=default_quarter)
        description = st.text_input("Goal Description *")
        key_results = st.text_area("Key Results (one per line, optional)")
        submitted = st.form_submit_button("Add Goal")

    if submitted:
        mid = name_map.get(member_name)
        if not mid:
            st.error("Select a team member.")
        elif not description:
            st.error("Description is required.")
        else:
            gid = db.add_goal(mid, quarter, description, key_results or None)
            set_toast("success", f"Goal #{gid} added for {member_name}.")
            st.rerun()


# -- Resources --------------------------------------------------------------

def page_agenda_templates():
    st.title("Meeting Agenda Templates")

    type_options = ["check_in", "coaching", "one_on_one", "quarterly_review"]
    type_labels = [templates.EVENT_TYPES[t]["label"] for t in type_options]

    col1, col2 = st.columns(2)
    with col1:
        label = st.selectbox("Meeting Type", type_labels)
    with col2:
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
    show_toast()

    st.info(
        "**SMTP Setup for Google Calendar Invitations**  \n"
        "To send invites via Gmail, generate an App Password at "
        "https://myaccount.google.com/apppasswords and use it below."
    )

    with st.form("config_form"):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("Your Name", value=db.get_config("manager_name", ""))
            email = st.text_input("Email Address", value=db.get_config("manager_email", ""))
            smtp_server = st.text_input("SMTP Server", value=db.get_config("smtp_server", "smtp.gmail.com"))
        with col2:
            smtp_port = st.text_input("SMTP Port", value=db.get_config("smtp_port", "587"))
            smtp_user = st.text_input("SMTP Username", value=db.get_config("smtp_user", ""))
            smtp_password = st.text_input("SMTP Password / App Password", type="password")
        submitted = st.form_submit_button("Save Configuration")

    if submitted:
        db.set_config("manager_name", name)
        db.set_config("manager_email", email)
        db.set_config("smtp_server", smtp_server)
        db.set_config("smtp_port", smtp_port)
        db.set_config("smtp_user", smtp_user)
        if smtp_password:
            db.set_config("smtp_password", smtp_password)
        set_toast("success", "Configuration saved.")
        st.rerun()

    st.subheader("Current Configuration")
    config = db.get_all_config()
    if config:
        for key, value in config.items():
            display = "********" if "password" in key else value
            st.text(f"{key}: {display}")
    else:
        st.caption("No configuration set yet.")


# ---------------------------------------------------------------------------
# Sidebar navigation & dispatch
# ---------------------------------------------------------------------------

def main():
    # ── Authentication gate ──────────────────────────────────────────────
    if not auth.require_auth():
        return  # Login page is shown; stop here

    # ── Sidebar ──────────────────────────────────────────────────────────
    with st.sidebar:
        st.title("Manager Tool")

        # Show logged-in user info
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

        st.caption("OVERVIEW")
        page = st.radio(
            "Navigate",
            options=[
                "Dashboard",
                "---1",
                "Schedule Event",
                "Upcoming Events",
                "Event History",
                "---2",
                "Team Roster",
                "Add Member",
                "---3",
                "Action Items",
                "Add Action",
                "Record Feedback",
                "---4",
                "Quarterly Goals",
                "Add Goal",
                "---5",
                "Agenda Templates",
                "Management Tips",
                "---6",
                "Configuration",
            ],
            format_func=lambda x: {
                "---1": "── ACTIVITIES ──",
                "---2": "── PEOPLE ──",
                "---3": "── TRACKING ──",
                "---4": "── GOALS ──",
                "---5": "── RESOURCES ──",
                "---6": "── SETTINGS ──",
            }.get(x, x),
            label_visibility="collapsed",
        )

    dispatch = {
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

    handler = dispatch.get(page)
    if handler:
        handler()
    else:
        page_dashboard()


main()

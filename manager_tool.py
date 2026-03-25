#!/usr/bin/env python3
"""
Manager Task Generator — CLI Application

Usage:
    python manager_tool.py                  # Interactive menu
    python manager_tool.py --gui            # Launch GUI
    python manager_tool.py <command>        # Direct command
"""

import argparse
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database as db
import calendar_service as cal
import templates

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
RESET = "\033[0m"


def header(text):
    print(f"\n{BOLD}{CYAN}{'=' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  {text}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 60}{RESET}")


def subheader(text):
    print(f"\n{BOLD}{text}{RESET}")
    print(f"{DIM}{'-' * 40}{RESET}")


def success(text):
    print(f"{GREEN}  [OK] {text}{RESET}")


def warn(text):
    print(f"{YELLOW}  [!] {text}{RESET}")


def error(text):
    print(f"{RED}  [ERROR] {text}{RESET}")


def info(text):
    print(f"  {text}")


def status_color(status):
    colors = {"scheduled": CYAN, "completed": GREEN, "cancelled": DIM,
              "pending": YELLOW, "in_progress": CYAN, "not_started": DIM,
              "met": GREEN, "exceeded": GREEN, "partially_met": YELLOW, "not_met": RED}
    color = colors.get(status, "")
    return f"{color}{status}{RESET}"


def prompt(text, default=None):
    suffix = f" [{default}]" if default else ""
    val = input(f"  {text}{suffix}: ").strip()
    return val if val else default


def prompt_choice(text, options):
    print(f"\n  {text}")
    for i, opt in enumerate(options, 1):
        print(f"    {i}. {opt}")
    while True:
        val = input(f"  Choice [1-{len(options)}]: ").strip()
        if val.isdigit() and 1 <= int(val) <= len(options):
            return int(val) - 1
        print(f"  Please enter a number between 1 and {len(options)}.")


def confirm(text):
    val = input(f"  {text} [y/N]: ").strip().lower()
    return val in ("y", "yes")


def print_table(rows, columns, widths=None):
    if not rows:
        info("(no records)")
        return
    if not widths:
        widths = [max(len(str(col)), max(len(str(r.get(col, ""))) for r in rows))
                  for col in columns]
        widths = [min(w + 2, 40) for w in widths]
    hdr = "  ".join(f"{BOLD}{str(col).ljust(w)}{RESET}" for col, w in zip(columns, widths))
    print(f"  {hdr}")
    print(f"  {'  '.join('-' * w for w in widths)}")
    for r in rows:
        vals = []
        for col, w in zip(columns, widths):
            v = str(r.get(col, ""))
            if col == "status":
                v = status_color(r.get(col, ""))
                v = v + " " * max(0, w - len(r.get(col, "")))
            else:
                v = v[:w].ljust(w)
            vals.append(v)
        print(f"  {'  '.join(vals)}")


# ---------------------------------------------------------------------------
# Interactive Menu
# ---------------------------------------------------------------------------

def _print_menu():
    print(f"""
  {BOLD}{CYAN}OVERVIEW{RESET}
    {CYAN}dashboard{RESET}      Weekly summary dashboard

  {BOLD}{CYAN}ACTIVITIES{RESET}
    {CYAN}schedule{RESET}       Schedule an event
    {CYAN}upcoming{RESET}       List upcoming events
    {CYAN}complete{RESET}       Complete an event
    {CYAN}history{RESET}        View event history
    {CYAN}invite{RESET}         Send calendar invite

  {BOLD}{CYAN}PEOPLE{RESET}
    {CYAN}add-member{RESET}     Add a team member
    {CYAN}roster{RESET}         View team roster
    {CYAN}member{RESET}         View member summary

  {BOLD}{CYAN}TRACKING{RESET}
    {CYAN}add-action{RESET}     Add an action item
    {CYAN}actions{RESET}        View pending actions
    {CYAN}feedback{RESET}       Record feedback (SBI)

  {BOLD}{CYAN}GOALS & REVIEWS{RESET}
    {CYAN}add-goal{RESET}       Add a quarterly goal
    {CYAN}goals{RESET}          View goals

  {BOLD}{CYAN}RESOURCES{RESET}
    {CYAN}agenda{RESET}         Meeting agenda template
    {CYAN}tips{RESET}           Management tips

  {BOLD}{CYAN}SETTINGS{RESET}
    {CYAN}config{RESET}         Configure email / SMTP
    {CYAN}show-config{RESET}    View configuration

    {DIM}menu{RESET}           Show this menu
    {DIM}quit{RESET}           Exit
""")


def interactive_menu():
    header("Manager Task Generator")
    _print_menu()

    actions = {
        "dashboard":   cmd_report_weekly,
        "schedule":    cmd_event_schedule,
        "upcoming":    cmd_event_list_upcoming,
        "complete":    cmd_event_complete,
        "history":     cmd_event_history,
        "invite":      cmd_event_invite_interactive,
        "add-member":  cmd_team_add,
        "roster":      cmd_team_list,
        "member":      cmd_team_show_interactive,
        "add-action":  cmd_action_add,
        "actions":     cmd_action_list_pending,
        "feedback":    cmd_feedback_add,
        "add-goal":    cmd_goals_add,
        "goals":       cmd_goals_list,
        "agenda":      cmd_agenda,
        "tips":        cmd_tips,
        "config":      cmd_config_setup,
        "show-config": cmd_config_show,
    }

    while True:
        try:
            choice = input(f"\n{BOLD}  > {RESET}").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\n")
            return

        if choice in ("quit", "exit", "q"):
            print("\n  Goodbye!\n")
            return
        if choice in ("menu", "help", "?"):
            _print_menu()
            continue

        action = actions.get(choice)
        if action:
            try:
                action()
            except (KeyboardInterrupt, EOFError):
                print("\n  (cancelled)")
        else:
            warn(f"Unknown command: '{choice}'. Type 'menu' to see available commands.")


# ---------------------------------------------------------------------------
# Config commands
# ---------------------------------------------------------------------------

def cmd_config_setup(args=None):
    header("Email & Profile Configuration")
    print(f"""
  {BOLD}SMTP Setup for Google Calendar Invitations{RESET}

  To send calendar invites via Gmail:
    1. Go to https://myaccount.google.com/apppasswords
    2. Generate an App Password for "Mail"
    3. Use that 16-character password below (not your regular password)
""")
    name = prompt("Your name", db.get_config("manager_name"))
    email = prompt("Your email address", db.get_config("manager_email"))
    smtp_server = prompt("SMTP server", db.get_config("smtp_server", "smtp.gmail.com"))
    smtp_port = prompt("SMTP port", db.get_config("smtp_port", "587"))
    smtp_user = prompt("SMTP username (usually your email)", db.get_config("smtp_user", email))
    smtp_password = prompt("SMTP password / App Password")

    if confirm("Save this configuration?"):
        db.set_config("manager_name", name)
        db.set_config("manager_email", email)
        db.set_config("smtp_server", smtp_server)
        db.set_config("smtp_port", smtp_port)
        db.set_config("smtp_user", smtp_user)
        if smtp_password:
            db.set_config("smtp_password", smtp_password)
        success("Configuration saved.")
    else:
        warn("Configuration not saved.")


def cmd_config_show(args=None):
    header("Current Configuration")
    config = db.get_all_config()
    if not config:
        warn("No configuration set. Run: python manager_tool.py config setup")
        return
    for key, value in config.items():
        display = "********" if "password" in key else value
        info(f"{key}: {display}")


# ---------------------------------------------------------------------------
# Team commands
# ---------------------------------------------------------------------------

def cmd_team_add(args=None):
    header("Add Team Member")
    name = prompt("Full name")
    if not name:
        error("Name is required.")
        return
    email = prompt("Email address")
    role = prompt("Role / title")
    start_date = prompt("Start date (YYYY-MM-DD)")
    notes = prompt("Notes (optional)")
    member_id = db.add_team_member(name, email, role, start_date, notes)
    success(f"Added {name} (ID: {member_id})")


def cmd_team_list(args=None):
    header("Team Roster")
    members = db.list_team_members()
    if not members:
        warn("No team members. Use 'add-member' to add someone.")
        return
    print_table(members, ["id", "name", "email", "role"])


def cmd_team_show_interactive(args=None):
    members = db.list_team_members()
    if not members:
        warn("No team members.")
        return
    cmd_team_list()
    member_id = prompt("\nEnter member ID to view details")
    if member_id and member_id.isdigit():
        _show_member_summary(int(member_id))


def cmd_team_show(args):
    member_id = args.id if hasattr(args, "id") else None
    if not member_id:
        error("Please provide a member ID.")
        return
    _show_member_summary(int(member_id))


def cmd_team_remove(args):
    member_id = args.id if hasattr(args, "id") else None
    if not member_id:
        error("Please provide a member ID.")
        return
    member = db.get_team_member(int(member_id))
    if not member:
        error(f"Member ID {member_id} not found.")
        return
    if confirm(f"Remove {member['name']} from your team?"):
        db.delete_team_member(int(member_id))
        success(f"Removed {member['name']}.")


def _show_member_summary(member_id):
    summary = db.get_member_summary(member_id)
    if not summary:
        error(f"Member ID {member_id} not found.")
        return
    m = summary["member"]
    header(f"Team Member: {m['name']}")
    info(f"Role:  {m.get('role', 'N/A')}")
    info(f"Email: {m.get('email', 'N/A')}")
    info(f"Start: {m.get('start_date', 'N/A')}")
    if m.get("notes"):
        info(f"Notes: {m['notes']}")
    if summary["recent_events"]:
        subheader("Recent Events")
        print_table(summary["recent_events"], ["id", "title", "event_type", "scheduled_date", "status"])
    if summary["goals"]:
        subheader("Goals")
        print_table(summary["goals"], ["id", "quarter", "description", "status"])
    if summary["feedback"]:
        subheader("Feedback History")
        for fb in summary["feedback"][:5]:
            ftype = f"{GREEN}+{RESET}" if fb["feedback_type"] == "positive" else f"{YELLOW}~{RESET}"
            info(f"  {ftype} [{fb['created_at'][:10]}] S: {fb.get('situation', 'N/A')}")
            info(f"      B: {fb.get('behavior', 'N/A')}")
            info(f"      I: {fb.get('impact', 'N/A')}")


# ---------------------------------------------------------------------------
# Event commands
# ---------------------------------------------------------------------------

def _pick_team_member(allow_none=True):
    members = db.list_team_members()
    if not members:
        if not allow_none:
            warn("No team members. Add one first with 'add-member'.")
            return None
        return None
    options = [f"{m['name']} ({m.get('role', 'N/A')})" for m in members]
    if allow_none:
        options.append("(no specific participant)")
    idx = prompt_choice("Select team member:", options)
    if allow_none and idx == len(members):
        return None
    return members[idx]


def cmd_event_schedule(args=None):
    header("Schedule an Event")
    type_options = list(templates.EVENT_TYPES.keys())
    type_labels = [templates.EVENT_TYPES[t]["label"] for t in type_options]
    idx = prompt_choice("Event type:", type_labels)
    event_type = type_options[idx]
    meta = templates.EVENT_TYPES[event_type]

    member = _pick_team_member()
    member_name = member["name"] if member else None
    member_id = member["id"] if member else None

    default_title = templates.get_default_title(event_type, member_name)
    title = prompt("Title", default_title)
    scheduled_date = prompt("Date (YYYY-MM-DD)")
    if not scheduled_date:
        error("Date is required.")
        return
    scheduled_time = prompt("Time (HH:MM, 24hr)", "10:00")
    duration = prompt("Duration (minutes)", str(meta["default_duration"]))
    location = prompt("Location / meeting link (optional)")

    use_template = confirm("Generate agenda from template?")
    agenda = None
    if use_template:
        agenda = templates.generate_agenda(event_type, member_name)
        print(f"\n{DIM}{agenda}{RESET}\n")

    event_id = db.create_event(
        title=title, event_type=event_type, scheduled_date=scheduled_date,
        scheduled_time=scheduled_time, team_member_id=member_id,
        duration_minutes=int(duration), location=location, agenda=agenda,
    )
    success(f"Event scheduled (ID: {event_id}): {title} on {scheduled_date} at {scheduled_time}")

    if confirm("Send a Google Calendar invitation for this event?"):
        event = db.get_event(event_id)
        _send_invite_for_event(event)


def cmd_event_list_upcoming(args=None):
    days = 14
    if hasattr(args, "days") and args and args.days:
        days = args.days
    header(f"Upcoming Events (next {days} days)")
    events = db.get_upcoming_events(days=days)
    if not events:
        info("No upcoming events scheduled.")
        return
    print_table(events, ["id", "title", "event_type", "scheduled_date", "scheduled_time",
                         "participant_name", "status"])


def cmd_event_history(args=None):
    header("Event History")
    events = db.list_events(status="completed", limit=20)
    if not events:
        info("No completed events yet.")
        return
    print_table(events, ["id", "title", "event_type", "scheduled_date", "participant_name", "status"])


def cmd_event_show(args):
    event_id = args.id if hasattr(args, "id") else None
    if not event_id:
        error("Please provide an event ID.")
        return
    event = db.get_event(int(event_id))
    if not event:
        error(f"Event ID {event_id} not found.")
        return
    _display_event(event)


def _display_event(event):
    header(f"Event #{event['id']}: {event['title']}")
    info(f"Type:        {templates.EVENT_TYPES.get(event['event_type'], {}).get('label', event['event_type'])}")
    info(f"Date:        {event['scheduled_date']} at {event['scheduled_time']}")
    info(f"Duration:    {event['duration_minutes']} minutes")
    info(f"Participant: {event.get('participant_name', 'N/A')}")
    info(f"Location:    {event.get('location') or 'N/A'}")
    info(f"Status:      {status_color(event['status'])}")
    info(f"Invite Sent: {'Yes' if event.get('calendar_invite_sent') else 'No'}")
    if event.get("agenda"):
        subheader("Agenda")
        for line in event["agenda"].split("\n"):
            print(f"  {DIM}{line}{RESET}")
    if event.get("notes"):
        subheader("Notes")
        for line in event["notes"].split("\n"):
            print(f"  {line}")
    actions = db.list_action_items(event_id=event["id"])
    if actions:
        subheader("Action Items")
        print_table(actions, ["id", "description", "assignee", "due_date", "status"])


def cmd_event_complete(args=None):
    event_id = None
    if hasattr(args, "id") and args and args.id:
        event_id = args.id
    else:
        events = db.list_events(status="scheduled", limit=20)
        if not events:
            warn("No scheduled events to complete.")
            return
        header("Complete an Event")
        print_table(events, ["id", "title", "event_type", "scheduled_date", "participant_name"])
        event_id = prompt("\nEnter event ID to mark complete")
    if not event_id:
        return
    event = db.get_event(int(event_id))
    if not event:
        error(f"Event ID {event_id} not found.")
        return
    print(f"\n  Completing: {BOLD}{event['title']}{RESET} ({event['scheduled_date']})")
    notes = prompt("Meeting notes (optional, press Enter to skip)")
    db.complete_event(int(event_id), notes=notes)
    success(f"Event #{event_id} marked as completed.")
    while confirm("Add an action item from this meeting?"):
        desc = prompt("  Action item description")
        assignee = prompt("  Assignee")
        due = prompt("  Due date (YYYY-MM-DD, optional)")
        if desc:
            aid = db.add_action_item(desc, event_id=int(event_id), assignee=assignee, due_date=due)
            success(f"Action item #{aid} added.")
    if event.get("team_member_id") and confirm("Record feedback from this meeting?"):
        _record_feedback_for_member(event["team_member_id"], event_id=int(event_id))


def cmd_event_cancel(args):
    event_id = args.id if hasattr(args, "id") else None
    if not event_id:
        error("Please provide an event ID.")
        return
    event = db.get_event(int(event_id))
    if not event:
        error(f"Event ID {event_id} not found.")
        return
    if confirm(f"Cancel '{event['title']}' on {event['scheduled_date']}?"):
        db.cancel_event(int(event_id))
        success(f"Event #{event_id} cancelled.")


def cmd_event_invite_interactive(args=None):
    events = db.list_events(status="scheduled", limit=20)
    if not events:
        warn("No scheduled events.")
        return
    header("Send Calendar Invitation")
    print_table(events, ["id", "title", "scheduled_date", "scheduled_time", "participant_name"])
    event_id = prompt("\nEnter event ID to send invite for")
    if event_id and event_id.isdigit():
        event = db.get_event(int(event_id))
        if event:
            _send_invite_for_event(event)


def cmd_event_invite(args):
    event_id = args.id if hasattr(args, "id") else None
    if not event_id:
        error("Please provide an event ID.")
        return
    event = db.get_event(int(event_id))
    if not event:
        error(f"Event ID {event_id} not found.")
        return
    _send_invite_for_event(event)


def _send_invite_for_event(event):
    send_to_self = confirm("Send invite to yourself (so you can add to your Google Calendar)?")
    send_to_participant = False
    if event.get("participant_email"):
        send_to_participant = confirm(
            f"Also send invite to {event['participant_name']} ({event['participant_email']})?"
        )
    if send_to_self:
        ok, msg = cal.send_invite_to_self(event)
        if ok:
            success(msg)
            db.update_event(event["id"], calendar_invite_sent=1)
        else:
            error(msg)
    if send_to_participant:
        ok, msg = cal.send_calendar_invite(event, event["participant_email"], event.get("participant_name"))
        if ok:
            success(msg)
        else:
            error(msg)
    if not send_to_self and not send_to_participant:
        if confirm("Save .ics file locally instead?"):
            manager_name = db.get_config("manager_name", "Manager")
            manager_email = db.get_config("manager_email", "")
            ics = cal.generate_ics(event, organizer_name=manager_name, organizer_email=manager_email,
                                   attendee_name=event.get("participant_name"), attendee_email=event.get("participant_email"))
            filepath = cal.save_ics_file(ics)
            success(f"Saved to: {filepath}")
            info("Open this file to add the event to your calendar.")


# ---------------------------------------------------------------------------
# Action Item commands
# ---------------------------------------------------------------------------

def cmd_action_add(args=None):
    header("Add Action Item")
    desc = prompt("Description")
    if not desc:
        error("Description is required.")
        return
    assignee = prompt("Assignee")
    due_date = prompt("Due date (YYYY-MM-DD, optional)")
    event_id = prompt("Related event ID (optional)")
    aid = db.add_action_item(desc, event_id=int(event_id) if event_id and event_id.isdigit() else None,
                             assignee=assignee, due_date=due_date)
    success(f"Action item #{aid} added.")


def cmd_action_list_pending(args=None):
    header("Pending Action Items")
    actions = db.get_pending_action_items()
    if not actions:
        success("No pending action items. You're caught up!")
        return
    print_table(actions, ["id", "description", "assignee", "due_date", "status", "event_title"])
    today = datetime.now().strftime("%Y-%m-%d")
    overdue = [a for a in actions if a.get("due_date") and a["due_date"] < today]
    if overdue:
        warn(f"{len(overdue)} overdue action item(s)!")


def cmd_action_complete(args=None):
    item_id = None
    if hasattr(args, "id") and args and args.id:
        item_id = args.id
    else:
        cmd_action_list_pending()
        item_id = prompt("\nEnter action item ID to complete")
    if item_id and str(item_id).isdigit():
        db.complete_action_item(int(item_id))
        success(f"Action item #{item_id} completed.")


# ---------------------------------------------------------------------------
# Feedback commands
# ---------------------------------------------------------------------------

def cmd_feedback_add(args=None):
    header("Record Feedback (SBI Framework)")
    member = _pick_team_member(allow_none=False)
    if not member:
        return
    _record_feedback_for_member(member["id"])


def _record_feedback_for_member(team_member_id, event_id=None):
    fb_type_idx = prompt_choice("Feedback type:", ["Positive", "Constructive"])
    fb_type = "positive" if fb_type_idx == 0 else "constructive"
    print(f"\n  {DIM}Use the SBI framework:{RESET}")
    print(f"  {DIM}Situation: When/where did this happen?{RESET}")
    print(f"  {DIM}Behavior:  What specifically did they do?{RESET}")
    print(f"  {DIM}Impact:    What was the result/effect?{RESET}\n")
    situation = prompt("Situation")
    behavior = prompt("Behavior")
    impact = prompt("Impact")
    fid = db.add_feedback(team_member_id, fb_type, situation, behavior, impact, event_id)
    success(f"Feedback #{fid} recorded.")


def cmd_feedback_list(args=None):
    header("Feedback History")
    feedback = db.list_feedback()
    if not feedback:
        info("No feedback recorded yet.")
        return
    for fb in feedback:
        ftype = f"{GREEN}POSITIVE{RESET}" if fb["feedback_type"] == "positive" else f"{YELLOW}CONSTRUCTIVE{RESET}"
        print(f"\n  #{fb['id']} | {ftype} | {fb['member_name']} | {fb['created_at'][:10]}")
        if fb.get("situation"):
            info(f"  Situation: {fb['situation']}")
        if fb.get("behavior"):
            info(f"  Behavior:  {fb['behavior']}")
        if fb.get("impact"):
            info(f"  Impact:    {fb['impact']}")


# ---------------------------------------------------------------------------
# Goals commands
# ---------------------------------------------------------------------------

def cmd_goals_add(args=None):
    header("Add Quarterly Goal")
    member = _pick_team_member(allow_none=False)
    if not member:
        return
    now = datetime.now()
    q = (now.month - 1) // 3 + 1
    default_quarter = f"Q{q} {now.year}"
    quarter = prompt("Quarter", default_quarter)
    description = prompt("Goal description")
    if not description:
        error("Description is required.")
        return
    key_results = prompt("Key results (comma-separated, optional)")
    gid = db.add_goal(member["id"], quarter, description, key_results)
    success(f"Goal #{gid} added for {member['name']}.")


def cmd_goals_list(args=None):
    header("Quarterly Goals")
    goals = db.list_goals()
    if not goals:
        info("No goals set yet.")
        return
    print_table(goals, ["id", "member_name", "quarter", "description", "status"])


def cmd_goals_update(args=None):
    goal_id = None
    if hasattr(args, "id") and args and args.id:
        goal_id = args.id
    else:
        cmd_goals_list()
        goal_id = prompt("\nEnter goal ID to update")
    if not goal_id or not str(goal_id).isdigit():
        return
    statuses = ["not_started", "in_progress", "met", "exceeded", "partially_met", "not_met"]
    idx = prompt_choice("New status:", statuses)
    db.update_goal(int(goal_id), status=statuses[idx])
    success(f"Goal #{goal_id} updated to '{statuses[idx]}'.")


# ---------------------------------------------------------------------------
# Report commands
# ---------------------------------------------------------------------------

def cmd_report_weekly(args=None):
    summary = db.get_weekly_summary()
    header("Weekly Dashboard")
    subheader("Upcoming Events This Week")
    if summary["upcoming_events"]:
        print_table(summary["upcoming_events"],
                    ["id", "title", "event_type", "scheduled_date", "scheduled_time", "participant_name"])
    else:
        info("No upcoming events this week.")
    subheader("Completed This Week")
    if summary["completed_events"]:
        print_table(summary["completed_events"],
                    ["id", "title", "event_type", "scheduled_date", "participant_name"])
    else:
        info("No events completed this week yet.")
    subheader("Pending Action Items")
    if summary["pending_actions"]:
        print_table(summary["pending_actions"], ["id", "description", "assignee", "due_date", "status"])
    else:
        success("No pending action items!")
    if summary["overdue_actions"]:
        subheader(f"{RED}Overdue Action Items{RESET}")
        print_table(summary["overdue_actions"], ["id", "description", "assignee", "due_date"])
    subheader("Tip of the Week")
    print(f"  {MAGENTA}{templates.get_random_tip()}{RESET}")


def cmd_report_member(args):
    member_id = args.id if hasattr(args, "id") else None
    if not member_id:
        error("Please provide a member ID.")
        return
    _show_member_summary(int(member_id))


# ---------------------------------------------------------------------------
# Tips & Agenda commands
# ---------------------------------------------------------------------------

def cmd_tips(args=None):
    header("Management Tips")
    count = 5
    if hasattr(args, "count") and args and args.count:
        count = args.count
    tips = templates.get_tips_by_count(count)
    for i, tip in enumerate(tips, 1):
        print(f"\n  {BOLD}{MAGENTA}{i}.{RESET} {tip}")
    if confirm("\nSee management anti-patterns?"):
        subheader("Common Anti-Patterns")
        for ap in templates.ANTI_PATTERNS:
            print(f"\n  {BOLD}{RED}{ap['name']}{RESET}")
            info(f"  Symptom: {ap['symptom']}")
            info(f"  Fix:     {GREEN}{ap['fix']}{RESET}")
    if confirm("\nSee the self-assessment scorecard?"):
        subheader("Weekly Manager Self-Assessment")
        for dim, question in templates.SELF_ASSESSMENT_DIMENSIONS:
            info(f"  {BOLD}{dim:15}{RESET}  {question}  ___/5")


def cmd_agenda(args=None):
    header("Meeting Agenda Template")
    type_options = ["check_in", "coaching", "one_on_one", "quarterly_review"]
    type_labels = [templates.EVENT_TYPES[t]["label"] for t in type_options]
    if hasattr(args, "type") and args and args.type:
        event_type = args.type
    else:
        idx = prompt_choice("Select meeting type:", type_labels)
        event_type = type_options[idx]
    name = prompt("Participant name (optional)")
    agenda = templates.generate_agenda(event_type, name)
    print(f"\n{agenda}")
    if event_type == "one_on_one" and confirm("\nSee topic bank for conversation starters?"):
        topics = templates.get_topic_suggestions()
        for category, questions in topics.items():
            subheader(category)
            for q in questions:
                info(f"  - {q}")


# ---------------------------------------------------------------------------
# Argparse CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="manager_tool",
        description="Manager Task Generator — Track meetings, coaching, and reviews",
    )
    sub = parser.add_subparsers(dest="command")

    config_p = sub.add_parser("config", help="Configuration")
    config_sub = config_p.add_subparsers(dest="config_cmd")
    config_sub.add_parser("setup", help="Configure SMTP and profile")
    config_sub.add_parser("show", help="Show current config")

    team_p = sub.add_parser("team", help="Manage team members")
    team_sub = team_p.add_subparsers(dest="team_cmd")
    team_sub.add_parser("add", help="Add a team member")
    team_sub.add_parser("list", help="List team members")
    ts = team_sub.add_parser("show", help="Show member details")
    ts.add_argument("id", type=int)
    tr = team_sub.add_parser("remove", help="Remove a team member")
    tr.add_argument("id", type=int)

    event_p = sub.add_parser("event", help="Manage events")
    event_sub = event_p.add_subparsers(dest="event_cmd")
    event_sub.add_parser("schedule", help="Schedule a new event")
    el = event_sub.add_parser("list", help="List upcoming events")
    el.add_argument("--days", type=int, default=14)
    event_sub.add_parser("history", help="View past events")
    es = event_sub.add_parser("show", help="Show event details")
    es.add_argument("id", type=int)
    ec = event_sub.add_parser("complete", help="Complete an event")
    ec.add_argument("id", type=int)
    eca = event_sub.add_parser("cancel", help="Cancel an event")
    eca.add_argument("id", type=int)
    ei = event_sub.add_parser("invite", help="Send calendar invite")
    ei.add_argument("id", type=int)

    action_p = sub.add_parser("action", help="Manage action items")
    action_sub = action_p.add_subparsers(dest="action_cmd")
    action_sub.add_parser("add", help="Add an action item")
    action_sub.add_parser("list", help="List pending actions")
    acl = action_sub.add_parser("complete", help="Complete an action")
    acl.add_argument("id", type=int)

    fb_p = sub.add_parser("feedback", help="Record and view feedback")
    fb_sub = fb_p.add_subparsers(dest="feedback_cmd")
    fb_sub.add_parser("add", help="Record feedback (SBI)")
    fb_sub.add_parser("list", help="List feedback history")

    goals_p = sub.add_parser("goals", help="Manage quarterly goals")
    goals_sub = goals_p.add_subparsers(dest="goals_cmd")
    goals_sub.add_parser("add", help="Add a goal")
    goals_sub.add_parser("list", help="List goals")
    gu = goals_sub.add_parser("update", help="Update goal status")
    gu.add_argument("id", type=int)

    report_p = sub.add_parser("report", help="View reports")
    report_sub = report_p.add_subparsers(dest="report_cmd")
    report_sub.add_parser("weekly", help="Weekly dashboard")
    rm = report_sub.add_parser("member", help="Member summary")
    rm.add_argument("id", type=int)

    tp = sub.add_parser("tips", help="Management tips")
    tp.add_argument("--count", type=int, default=5)

    ag = sub.add_parser("agenda", help="Print meeting agenda template")
    ag.add_argument("type", nargs="?", choices=["check_in", "coaching", "one_on_one", "quarterly_review"])

    return parser


def main():
    db.init_db()

    if "--gui" in sys.argv:
        import gui
        gui.main()
        return

    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        interactive_menu()
        return

    dispatch = {
        "config": {"setup": cmd_config_setup, "show": cmd_config_show},
        "team": {"add": cmd_team_add, "list": cmd_team_list, "show": cmd_team_show, "remove": cmd_team_remove},
        "event": {"schedule": cmd_event_schedule, "list": cmd_event_list_upcoming, "history": cmd_event_history,
                  "show": cmd_event_show, "complete": cmd_event_complete, "cancel": cmd_event_cancel, "invite": cmd_event_invite},
        "action": {"add": cmd_action_add, "list": cmd_action_list_pending, "complete": cmd_action_complete},
        "feedback": {"add": cmd_feedback_add, "list": cmd_feedback_list},
        "goals": {"add": cmd_goals_add, "list": cmd_goals_list, "update": cmd_goals_update},
        "report": {"weekly": cmd_report_weekly, "member": cmd_report_member},
        "tips": {"_direct": cmd_tips},
        "agenda": {"_direct": cmd_agenda},
    }

    cmd_group = dispatch.get(args.command)
    if not cmd_group:
        parser.print_help()
        return
    if "_direct" in cmd_group:
        cmd_group["_direct"](args)
        return
    subcmd_attr = f"{args.command}_cmd"
    subcmd = getattr(args, subcmd_attr, None)
    handler = cmd_group.get(subcmd) if subcmd else None
    if handler:
        handler(args)
    else:
        parser.parse_args([args.command, "--help"])


if __name__ == "__main__":
    main()

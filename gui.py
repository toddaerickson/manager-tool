#!/usr/bin/env python3
"""
Manager Task Generator — GUI Application
Dark-themed sidebar navigation with tkinter.
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database as db
import templates

# ---------------------------------------------------------------------------
# Color Palette
# ---------------------------------------------------------------------------
BG_DARK = "#1e1e2e"
BG_SIDEBAR = "#181825"
BG_CARD = "#313244"
BG_INPUT = "#45475a"
FG_TEXT = "#cdd6f4"
FG_DIM = "#6c7086"
FG_ACCENT = "#89b4fa"
FG_GREEN = "#a6e3a1"
FG_YELLOW = "#f9e2af"
FG_RED = "#f38ba8"
FG_MAUVE = "#cba6f7"
HOVER_BG = "#45475a"
ACTIVE_BG = "#585b70"
BORDER_COLOR = "#313244"

FONT_FAMILY = "Segoe UI"
FONT_HEADING = (FONT_FAMILY, 14, "bold")
FONT_SUBHEADING = (FONT_FAMILY, 11, "bold")
FONT_BODY = (FONT_FAMILY, 10)
FONT_SMALL = (FONT_FAMILY, 9)
FONT_SIDEBAR = (FONT_FAMILY, 10)
FONT_SIDEBAR_HEADER = (FONT_FAMILY, 8, "bold")


class ManagerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Manager Task Generator")
        self.geometry("1100x700")
        self.minsize(900, 550)
        self.configure(bg=BG_DARK)

        db.init_db()

        self._build_layout()
        self._show_panel("dashboard")

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build_layout(self):
        # Sidebar
        self.sidebar = tk.Frame(self, bg=BG_SIDEBAR, width=220)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        # Brand
        brand = tk.Label(self.sidebar, text="Manager Tool",
                         font=(FONT_FAMILY, 16, "bold"), fg=FG_ACCENT,
                         bg=BG_SIDEBAR, pady=18)
        brand.pack(fill="x")

        sep = tk.Frame(self.sidebar, bg=BORDER_COLOR, height=1)
        sep.pack(fill="x", padx=12)

        # Navigation items grouped by section
        nav_sections = [
            ("OVERVIEW", [
                ("dashboard", "Dashboard"),
            ]),
            ("ACTIVITIES", [
                ("schedule", "Schedule Event"),
                ("upcoming", "Upcoming Events"),
                ("history", "Event History"),
            ]),
            ("PEOPLE", [
                ("roster", "Team Roster"),
                ("add_member", "Add Member"),
            ]),
            ("TRACKING", [
                ("actions", "Action Items"),
                ("add_action", "Add Action"),
                ("feedback", "Record Feedback"),
            ]),
            ("GOALS", [
                ("goals", "Quarterly Goals"),
                ("add_goal", "Add Goal"),
            ]),
            ("RESOURCES", [
                ("agenda", "Agenda Templates"),
                ("tips", "Management Tips"),
            ]),
            ("SETTINGS", [
                ("config", "Configuration"),
            ]),
        ]

        scroll_frame = tk.Frame(self.sidebar, bg=BG_SIDEBAR)
        scroll_frame.pack(fill="both", expand=True, pady=6)

        self._nav_buttons = {}
        for section_name, items in nav_sections:
            header = tk.Label(scroll_frame, text=section_name,
                              font=FONT_SIDEBAR_HEADER, fg=FG_DIM,
                              bg=BG_SIDEBAR, anchor="w", padx=20, pady=(10, 2))
            header.pack(fill="x")
            for key, label in items:
                btn = tk.Label(scroll_frame, text=f"  {label}",
                               font=FONT_SIDEBAR, fg=FG_TEXT, bg=BG_SIDEBAR,
                               anchor="w", padx=20, pady=5, cursor="hand2")
                btn.pack(fill="x")
                btn.bind("<Enter>", lambda e, b=btn: b.configure(bg=HOVER_BG))
                btn.bind("<Leave>", lambda e, b=btn, k=key: b.configure(
                    bg=ACTIVE_BG if k == self._active_panel else BG_SIDEBAR))
                btn.bind("<Button-1>", lambda e, k=key: self._show_panel(k))
                self._nav_buttons[key] = btn

        # Main content area
        self.content = tk.Frame(self, bg=BG_DARK)
        self.content.pack(side="right", fill="both", expand=True)

        self._active_panel = None

    def _show_panel(self, panel_name):
        # Update sidebar highlight
        if self._active_panel and self._active_panel in self._nav_buttons:
            self._nav_buttons[self._active_panel].configure(bg=BG_SIDEBAR)
        self._active_panel = panel_name
        if panel_name in self._nav_buttons:
            self._nav_buttons[panel_name].configure(bg=ACTIVE_BG)

        # Clear content
        for w in self.content.winfo_children():
            w.destroy()

        panels = {
            "dashboard": self._panel_dashboard,
            "schedule": self._panel_schedule,
            "upcoming": self._panel_upcoming,
            "history": self._panel_history,
            "roster": self._panel_roster,
            "add_member": self._panel_add_member,
            "actions": self._panel_actions,
            "add_action": self._panel_add_action,
            "feedback": self._panel_feedback,
            "goals": self._panel_goals,
            "add_goal": self._panel_add_goal,
            "agenda": self._panel_agenda,
            "tips": self._panel_tips,
            "config": self._panel_config,
        }
        builder = panels.get(panel_name)
        if builder:
            builder()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _heading(self, parent, text):
        lbl = tk.Label(parent, text=text, font=FONT_HEADING,
                       fg=FG_ACCENT, bg=BG_DARK, anchor="w")
        lbl.pack(fill="x", padx=20, pady=(18, 4))
        sep = tk.Frame(parent, bg=BORDER_COLOR, height=1)
        sep.pack(fill="x", padx=20, pady=(0, 10))

    def _card(self, parent, **pack_kw):
        frame = tk.Frame(parent, bg=BG_CARD, padx=14, pady=10)
        defaults = {"fill": "x", "padx": 20, "pady": 4}
        defaults.update(pack_kw)
        frame.pack(**defaults)
        return frame

    def _label(self, parent, text, **kw):
        defaults = {"font": FONT_BODY, "fg": FG_TEXT, "bg": parent["bg"], "anchor": "w"}
        defaults.update(kw)
        lbl = tk.Label(parent, text=text, **defaults)
        lbl.pack(fill="x")
        return lbl

    def _entry(self, parent, label_text, default=""):
        row = tk.Frame(parent, bg=parent["bg"])
        row.pack(fill="x", pady=3)
        lbl = tk.Label(row, text=label_text, font=FONT_BODY, fg=FG_DIM,
                       bg=parent["bg"], width=18, anchor="e")
        lbl.pack(side="left")
        var = tk.StringVar(value=default)
        ent = tk.Entry(row, textvariable=var, font=FONT_BODY, bg=BG_INPUT,
                       fg=FG_TEXT, insertbackground=FG_TEXT, relief="flat",
                       highlightthickness=1, highlightbackground=BORDER_COLOR)
        ent.pack(side="left", fill="x", expand=True, padx=(6, 0), ipady=3)
        return var

    def _combo(self, parent, label_text, values, default_idx=0):
        row = tk.Frame(parent, bg=parent["bg"])
        row.pack(fill="x", pady=3)
        lbl = tk.Label(row, text=label_text, font=FONT_BODY, fg=FG_DIM,
                       bg=parent["bg"], width=18, anchor="e")
        lbl.pack(side="left")
        var = tk.StringVar(value=values[default_idx] if values else "")
        combo = ttk.Combobox(row, textvariable=var, values=values,
                             state="readonly", font=FONT_BODY)
        combo.pack(side="left", fill="x", expand=True, padx=(6, 0))
        return var

    def _button(self, parent, text, command, color=FG_ACCENT):
        btn = tk.Label(parent, text=f"  {text}  ", font=FONT_BODY,
                       fg=BG_DARK, bg=color, cursor="hand2", padx=12, pady=4)
        btn.pack(pady=8)
        btn.bind("<Button-1>", lambda e: command())
        return btn

    def _build_table(self, parent, columns, rows, widths=None):
        container = tk.Frame(parent, bg=BG_CARD)
        container.pack(fill="both", expand=True, padx=20, pady=4)

        # Header row
        hdr = tk.Frame(container, bg=BG_CARD)
        hdr.pack(fill="x")
        for i, col in enumerate(columns):
            w = widths[i] if widths else 14
            tk.Label(hdr, text=col.replace("_", " ").title(), font=FONT_SUBHEADING,
                     fg=FG_ACCENT, bg=BG_CARD, width=w, anchor="w").pack(side="left", padx=4)
        sep = tk.Frame(container, bg=BORDER_COLOR, height=1)
        sep.pack(fill="x", pady=2)

        # Data rows
        for row in rows:
            rf = tk.Frame(container, bg=BG_CARD)
            rf.pack(fill="x")
            for i, col in enumerate(columns):
                w = widths[i] if widths else 14
                val = str(row.get(col, ""))
                fg = FG_TEXT
                if col == "status":
                    fg = {
                        "scheduled": FG_ACCENT, "completed": FG_GREEN,
                        "cancelled": FG_DIM, "pending": FG_YELLOW,
                        "in_progress": FG_ACCENT, "met": FG_GREEN,
                        "exceeded": FG_GREEN, "not_met": FG_RED,
                        "not_started": FG_DIM,
                    }.get(val, FG_TEXT)
                tk.Label(rf, text=val[:30], font=FONT_SMALL, fg=fg,
                         bg=BG_CARD, width=w, anchor="w").pack(side="left", padx=4)

        if not rows:
            tk.Label(container, text="No records found.", font=FONT_BODY,
                     fg=FG_DIM, bg=BG_CARD, pady=10).pack()

        return container

    def _scrollable_frame(self, parent):
        canvas = tk.Canvas(parent, bg=BG_DARK, highlightthickness=0)
        scrollbar = tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        frame = tk.Frame(canvas, bg=BG_DARK)
        frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=frame, anchor="nw",
                             tags="frame_window")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(
            "frame_window", width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Mouse wheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _on_mousewheel_linux(event):
            if event.num == 4:
                canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                canvas.yview_scroll(1, "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        canvas.bind_all("<Button-4>", _on_mousewheel_linux)
        canvas.bind_all("<Button-5>", _on_mousewheel_linux)

        return frame

    def _get_member_names(self):
        members = db.list_team_members()
        return {m["name"]: m["id"] for m in members}

    # ------------------------------------------------------------------
    # Dashboard Panel
    # ------------------------------------------------------------------
    def _panel_dashboard(self):
        frame = self._scrollable_frame(self.content)
        self._heading(frame, "Weekly Dashboard")

        summary = db.get_weekly_summary()

        # Stats row
        stats_frame = tk.Frame(frame, bg=BG_DARK)
        stats_frame.pack(fill="x", padx=20, pady=6)

        stats = [
            ("Upcoming", len(summary["upcoming_events"]), FG_ACCENT),
            ("Completed", len(summary["completed_events"]), FG_GREEN),
            ("Pending Actions", len(summary["pending_actions"]), FG_YELLOW),
            ("Overdue", len(summary["overdue_actions"]), FG_RED),
        ]
        for label, count, color in stats:
            card = tk.Frame(stats_frame, bg=BG_CARD, padx=18, pady=10)
            card.pack(side="left", expand=True, fill="x", padx=4)
            tk.Label(card, text=str(count), font=(FONT_FAMILY, 22, "bold"),
                     fg=color, bg=BG_CARD).pack()
            tk.Label(card, text=label, font=FONT_SMALL, fg=FG_DIM,
                     bg=BG_CARD).pack()

        # Upcoming events
        if summary["upcoming_events"]:
            self._label(frame, "Upcoming Events This Week",
                        font=FONT_SUBHEADING, fg=FG_ACCENT, padx=20, pady=(12, 2))
            self._build_table(frame,
                              ["id", "title", "event_type", "scheduled_date", "scheduled_time", "participant_name"],
                              summary["upcoming_events"],
                              [4, 22, 12, 10, 8, 14])

        # Pending actions
        if summary["pending_actions"]:
            self._label(frame, "Pending Action Items",
                        font=FONT_SUBHEADING, fg=FG_YELLOW, padx=20, pady=(12, 2))
            self._build_table(frame,
                              ["id", "description", "assignee", "due_date", "status"],
                              summary["pending_actions"],
                              [4, 28, 12, 10, 10])

        # Overdue
        if summary["overdue_actions"]:
            self._label(frame, "Overdue Action Items",
                        font=FONT_SUBHEADING, fg=FG_RED, padx=20, pady=(12, 2))
            self._build_table(frame,
                              ["id", "description", "assignee", "due_date"],
                              summary["overdue_actions"],
                              [4, 30, 14, 10])

        # Tip
        card = self._card(frame, pady=10)
        tk.Label(card, text="Tip of the Week", font=FONT_SUBHEADING,
                 fg=FG_MAUVE, bg=BG_CARD, anchor="w").pack(fill="x")
        tk.Label(card, text=templates.get_random_tip(), font=FONT_BODY,
                 fg=FG_TEXT, bg=BG_CARD, anchor="w", wraplength=700,
                 justify="left").pack(fill="x", pady=(4, 0))

    # ------------------------------------------------------------------
    # Schedule Event Panel
    # ------------------------------------------------------------------
    def _panel_schedule(self):
        frame = self._scrollable_frame(self.content)
        self._heading(frame, "Schedule an Event")
        card = self._card(frame)

        type_labels = [templates.EVENT_TYPES[t]["label"] for t in templates.EVENT_TYPES]
        type_keys = list(templates.EVENT_TYPES.keys())

        type_var = self._combo(card, "Event Type:", type_labels)
        members = self._get_member_names()
        member_names = ["(none)"] + list(members.keys())
        member_var = self._combo(card, "Participant:", member_names)
        title_var = self._entry(card, "Title:")
        date_var = self._entry(card, "Date (YYYY-MM-DD):",
                               datetime.now().strftime("%Y-%m-%d"))
        time_var = self._entry(card, "Time (HH:MM):", "10:00")
        duration_var = self._entry(card, "Duration (min):", "30")
        location_var = self._entry(card, "Location:")

        def save():
            type_label = type_var.get()
            type_idx = type_labels.index(type_label) if type_label in type_labels else 0
            event_type = type_keys[type_idx]
            member_name = member_var.get()
            member_id = members.get(member_name) if member_name != "(none)" else None
            title = title_var.get() or templates.get_default_title(
                event_type, member_name if member_name != "(none)" else None)
            date = date_var.get()
            if not date:
                messagebox.showerror("Error", "Date is required.")
                return
            eid = db.create_event(
                title=title, event_type=event_type,
                scheduled_date=date, scheduled_time=time_var.get() or "10:00",
                team_member_id=member_id,
                duration_minutes=int(duration_var.get() or 30),
                location=location_var.get() or None,
            )
            messagebox.showinfo("Success", f"Event #{eid} scheduled: {title}")
            self._show_panel("upcoming")

        self._button(card, "Schedule Event", save, FG_GREEN)

    # ------------------------------------------------------------------
    # Upcoming Events Panel
    # ------------------------------------------------------------------
    def _panel_upcoming(self):
        frame = self._scrollable_frame(self.content)
        self._heading(frame, "Upcoming Events (Next 14 Days)")
        events = db.get_upcoming_events(days=14)
        self._build_table(frame,
                          ["id", "title", "event_type", "scheduled_date",
                           "scheduled_time", "participant_name", "status"],
                          events,
                          [4, 20, 12, 10, 8, 14, 10])

        if events:
            card = self._card(frame)
            complete_var = self._entry(card, "Event ID to complete:")

            def complete_event():
                eid = complete_var.get()
                if eid and eid.isdigit():
                    db.complete_event(int(eid))
                    messagebox.showinfo("Done", f"Event #{eid} marked completed.")
                    self._show_panel("upcoming")

            self._button(card, "Mark Complete", complete_event, FG_GREEN)

    # ------------------------------------------------------------------
    # Event History Panel
    # ------------------------------------------------------------------
    def _panel_history(self):
        frame = self._scrollable_frame(self.content)
        self._heading(frame, "Event History")
        events = db.list_events(status="completed", limit=30)
        self._build_table(frame,
                          ["id", "title", "event_type", "scheduled_date",
                           "participant_name", "status"],
                          events,
                          [4, 22, 12, 10, 14, 10])

    # ------------------------------------------------------------------
    # Team Roster Panel
    # ------------------------------------------------------------------
    def _panel_roster(self):
        frame = self._scrollable_frame(self.content)
        self._heading(frame, "Team Roster")
        members = db.list_team_members()
        self._build_table(frame,
                          ["id", "name", "email", "role", "start_date"],
                          members,
                          [4, 16, 20, 16, 10])

        if members:
            card = self._card(frame)
            detail_var = self._entry(card, "Member ID for details:")

            def show_detail():
                mid = detail_var.get()
                if mid and mid.isdigit():
                    summary = db.get_member_summary(int(mid))
                    if not summary:
                        messagebox.showerror("Error", f"Member #{mid} not found.")
                        return
                    self._show_member_detail(summary)

            self._button(card, "View Details", show_detail, FG_ACCENT)

    def _show_member_detail(self, summary):
        for w in self.content.winfo_children():
            w.destroy()
        frame = self._scrollable_frame(self.content)

        m = summary["member"]
        self._heading(frame, f"Team Member: {m['name']}")

        card = self._card(frame)
        self._label(card, f"Role:  {m.get('role', 'N/A')}")
        self._label(card, f"Email: {m.get('email', 'N/A')}")
        self._label(card, f"Start: {m.get('start_date', 'N/A')}")
        if m.get("notes"):
            self._label(card, f"Notes: {m['notes']}")

        if summary["recent_events"]:
            self._label(frame, "Recent Events", font=FONT_SUBHEADING,
                        fg=FG_ACCENT, padx=20, pady=(10, 2))
            self._build_table(frame,
                              ["id", "title", "event_type", "scheduled_date", "status"],
                              summary["recent_events"],
                              [4, 20, 12, 10, 10])

        if summary["goals"]:
            self._label(frame, "Goals", font=FONT_SUBHEADING,
                        fg=FG_MAUVE, padx=20, pady=(10, 2))
            self._build_table(frame,
                              ["id", "quarter", "description", "status"],
                              summary["goals"],
                              [4, 10, 30, 10])

        if summary["feedback"]:
            self._label(frame, "Feedback History", font=FONT_SUBHEADING,
                        fg=FG_YELLOW, padx=20, pady=(10, 2))
            for fb in summary["feedback"][:5]:
                c = self._card(frame)
                fb_color = FG_GREEN if fb["feedback_type"] == "positive" else FG_YELLOW
                self._label(c, f"[{fb['feedback_type'].upper()}] {fb['created_at'][:10]}",
                            fg=fb_color)
                self._label(c, f"Situation: {fb.get('situation', 'N/A')}", fg=FG_DIM)
                self._label(c, f"Behavior:  {fb.get('behavior', 'N/A')}", fg=FG_DIM)
                self._label(c, f"Impact:    {fb.get('impact', 'N/A')}", fg=FG_DIM)

        # Back button
        back_btn = tk.Label(frame, text="  Back to Roster  ", font=FONT_BODY,
                            fg=BG_DARK, bg=FG_ACCENT, cursor="hand2", padx=12, pady=4)
        back_btn.pack(padx=20, pady=10)
        back_btn.bind("<Button-1>", lambda e: self._show_panel("roster"))

    # ------------------------------------------------------------------
    # Add Member Panel
    # ------------------------------------------------------------------
    def _panel_add_member(self):
        frame = self._scrollable_frame(self.content)
        self._heading(frame, "Add Team Member")
        card = self._card(frame)

        name_var = self._entry(card, "Full Name:")
        email_var = self._entry(card, "Email:")
        role_var = self._entry(card, "Role / Title:")
        start_var = self._entry(card, "Start Date:", datetime.now().strftime("%Y-%m-%d"))
        notes_var = self._entry(card, "Notes:")

        def save():
            name = name_var.get()
            if not name:
                messagebox.showerror("Error", "Name is required.")
                return
            mid = db.add_team_member(name, email_var.get() or None,
                                     role_var.get() or None,
                                     start_var.get() or None,
                                     notes_var.get() or None)
            messagebox.showinfo("Success", f"Added {name} (ID: {mid})")
            self._show_panel("roster")

        self._button(card, "Add Member", save, FG_GREEN)

    # ------------------------------------------------------------------
    # Action Items Panel
    # ------------------------------------------------------------------
    def _panel_actions(self):
        frame = self._scrollable_frame(self.content)
        self._heading(frame, "Pending Action Items")
        actions = db.get_pending_action_items()

        today = datetime.now().strftime("%Y-%m-%d")
        overdue = [a for a in actions if a.get("due_date") and a["due_date"] < today]

        self._build_table(frame,
                          ["id", "description", "assignee", "due_date", "status"],
                          actions,
                          [4, 28, 14, 10, 10])

        if overdue:
            self._label(frame, f"  {len(overdue)} overdue action item(s)!",
                        fg=FG_RED, padx=20)

        if actions:
            card = self._card(frame)
            complete_var = self._entry(card, "Action ID to complete:")

            def complete():
                aid = complete_var.get()
                if aid and aid.isdigit():
                    db.complete_action_item(int(aid))
                    messagebox.showinfo("Done", f"Action #{aid} completed.")
                    self._show_panel("actions")

            self._button(card, "Mark Complete", complete, FG_GREEN)

    # ------------------------------------------------------------------
    # Add Action Panel
    # ------------------------------------------------------------------
    def _panel_add_action(self):
        frame = self._scrollable_frame(self.content)
        self._heading(frame, "Add Action Item")
        card = self._card(frame)

        desc_var = self._entry(card, "Description:")
        assignee_var = self._entry(card, "Assignee:")
        due_var = self._entry(card, "Due Date (YYYY-MM-DD):")
        event_var = self._entry(card, "Related Event ID:")

        def save():
            desc = desc_var.get()
            if not desc:
                messagebox.showerror("Error", "Description is required.")
                return
            eid_str = event_var.get()
            eid = int(eid_str) if eid_str and eid_str.isdigit() else None
            aid = db.add_action_item(desc, event_id=eid,
                                     assignee=assignee_var.get() or None,
                                     due_date=due_var.get() or None)
            messagebox.showinfo("Success", f"Action item #{aid} added.")
            self._show_panel("actions")

        self._button(card, "Add Action Item", save, FG_GREEN)

    # ------------------------------------------------------------------
    # Feedback Panel
    # ------------------------------------------------------------------
    def _panel_feedback(self):
        frame = self._scrollable_frame(self.content)
        self._heading(frame, "Record Feedback (SBI Framework)")
        card = self._card(frame)

        members = self._get_member_names()
        if not members:
            self._label(card, "No team members yet. Add one first.",
                        fg=FG_YELLOW)
            return

        member_var = self._combo(card, "Team Member:", list(members.keys()))
        type_var = self._combo(card, "Feedback Type:", ["Positive", "Constructive"])

        self._label(card, "", fg=FG_DIM)
        self._label(card, "  SBI Framework:", font=FONT_SUBHEADING, fg=FG_MAUVE)
        self._label(card, "  Situation: When/where did this happen?", fg=FG_DIM)
        self._label(card, "  Behavior:  What specifically did they do?", fg=FG_DIM)
        self._label(card, "  Impact:    What was the result/effect?", fg=FG_DIM)
        self._label(card, "", fg=FG_DIM)

        situation_var = self._entry(card, "Situation:")
        behavior_var = self._entry(card, "Behavior:")
        impact_var = self._entry(card, "Impact:")

        def save():
            name = member_var.get()
            mid = members.get(name)
            if not mid:
                messagebox.showerror("Error", "Select a team member.")
                return
            fb_type = "positive" if type_var.get() == "Positive" else "constructive"
            fid = db.add_feedback(mid, fb_type,
                                  situation_var.get() or None,
                                  behavior_var.get() or None,
                                  impact_var.get() or None)
            messagebox.showinfo("Success", f"Feedback #{fid} recorded.")
            self._show_panel("dashboard")

        self._button(card, "Save Feedback", save, FG_GREEN)

    # ------------------------------------------------------------------
    # Goals Panel
    # ------------------------------------------------------------------
    def _panel_goals(self):
        frame = self._scrollable_frame(self.content)
        self._heading(frame, "Quarterly Goals")
        goals = db.list_goals()
        self._build_table(frame,
                          ["id", "member_name", "quarter", "description", "status"],
                          goals,
                          [4, 14, 8, 28, 10])

        if goals:
            card = self._card(frame)
            goal_id_var = self._entry(card, "Goal ID to update:")
            statuses = ["not_started", "in_progress", "met", "exceeded",
                        "partially_met", "not_met"]
            status_var = self._combo(card, "New Status:", statuses)

            def update():
                gid = goal_id_var.get()
                if gid and gid.isdigit():
                    db.update_goal(int(gid), status=status_var.get())
                    messagebox.showinfo("Done", f"Goal #{gid} updated.")
                    self._show_panel("goals")

            self._button(card, "Update Status", update, FG_ACCENT)

    # ------------------------------------------------------------------
    # Add Goal Panel
    # ------------------------------------------------------------------
    def _panel_add_goal(self):
        frame = self._scrollable_frame(self.content)
        self._heading(frame, "Add Quarterly Goal")
        card = self._card(frame)

        members = self._get_member_names()
        if not members:
            self._label(card, "No team members yet. Add one first.", fg=FG_YELLOW)
            return

        member_var = self._combo(card, "Team Member:", list(members.keys()))
        now = datetime.now()
        q = (now.month - 1) // 3 + 1
        quarter_var = self._entry(card, "Quarter:", f"Q{q} {now.year}")
        desc_var = self._entry(card, "Goal Description:")
        kr_var = self._entry(card, "Key Results (comma-sep):")

        def save():
            name = member_var.get()
            mid = members.get(name)
            if not mid:
                messagebox.showerror("Error", "Select a team member.")
                return
            desc = desc_var.get()
            if not desc:
                messagebox.showerror("Error", "Description is required.")
                return
            gid = db.add_goal(mid, quarter_var.get(), desc,
                              kr_var.get() or None)
            messagebox.showinfo("Success", f"Goal #{gid} added for {name}.")
            self._show_panel("goals")

        self._button(card, "Add Goal", save, FG_GREEN)

    # ------------------------------------------------------------------
    # Agenda Templates Panel
    # ------------------------------------------------------------------
    def _panel_agenda(self):
        frame = self._scrollable_frame(self.content)
        self._heading(frame, "Meeting Agenda Templates")

        type_options = ["check_in", "coaching", "one_on_one", "quarterly_review"]
        type_labels = [templates.EVENT_TYPES[t]["label"] for t in type_options]

        card = self._card(frame)
        type_var = self._combo(card, "Meeting Type:", type_labels)

        text_frame = tk.Frame(frame, bg=BG_DARK)
        text_frame.pack(fill="both", expand=True, padx=20, pady=8)

        text_widget = scrolledtext.ScrolledText(
            text_frame, font=FONT_BODY, bg=BG_CARD, fg=FG_TEXT,
            insertbackground=FG_TEXT, relief="flat", wrap="word",
            height=20)
        text_widget.pack(fill="both", expand=True)

        def show_agenda():
            label = type_var.get()
            idx = type_labels.index(label) if label in type_labels else 0
            event_type = type_options[idx]
            agenda = templates.generate_agenda(event_type, "Team Member")
            text_widget.delete("1.0", "end")
            text_widget.insert("1.0", agenda)

        self._button(card, "Generate Agenda", show_agenda, FG_ACCENT)
        # Show default
        text_widget.insert("1.0",
                           templates.generate_agenda("one_on_one", "Team Member"))

    # ------------------------------------------------------------------
    # Tips Panel
    # ------------------------------------------------------------------
    def _panel_tips(self):
        frame = self._scrollable_frame(self.content)
        self._heading(frame, "Management Tips")

        tips = templates.get_tips_by_count(8)
        for i, tip in enumerate(tips, 1):
            card = self._card(frame)
            tk.Label(card, text=f"{i}.", font=FONT_SUBHEADING, fg=FG_MAUVE,
                     bg=BG_CARD, anchor="nw", width=3).pack(side="left", anchor="n")
            tk.Label(card, text=tip, font=FONT_BODY, fg=FG_TEXT, bg=BG_CARD,
                     anchor="w", wraplength=650, justify="left").pack(
                         side="left", fill="x", expand=True)

        # Anti-patterns
        self._label(frame, "Common Anti-Patterns", font=FONT_SUBHEADING,
                    fg=FG_RED, padx=20, pady=(16, 4))
        for ap in templates.ANTI_PATTERNS:
            card = self._card(frame)
            self._label(card, ap["name"], font=FONT_SUBHEADING, fg=FG_RED)
            self._label(card, f"Symptom: {ap['symptom']}", fg=FG_DIM)
            self._label(card, f"Fix: {ap['fix']}", fg=FG_GREEN)

    # ------------------------------------------------------------------
    # Configuration Panel
    # ------------------------------------------------------------------
    def _panel_config(self):
        frame = self._scrollable_frame(self.content)
        self._heading(frame, "Email & Profile Configuration")

        card = self._card(frame)
        self._label(card, "SMTP Setup for Google Calendar Invitations",
                    font=FONT_SUBHEADING, fg=FG_MAUVE)
        self._label(card, "Use a Gmail App Password (not your regular password).",
                    fg=FG_DIM)
        self._label(card, "", fg=FG_DIM)

        name_var = self._entry(card, "Your Name:",
                               db.get_config("manager_name", ""))
        email_var = self._entry(card, "Email:",
                                db.get_config("manager_email", ""))
        smtp_server_var = self._entry(card, "SMTP Server:",
                                      db.get_config("smtp_server", "smtp.gmail.com"))
        smtp_port_var = self._entry(card, "SMTP Port:",
                                    db.get_config("smtp_port", "587"))
        smtp_user_var = self._entry(card, "SMTP Username:",
                                    db.get_config("smtp_user", ""))
        smtp_pass_var = self._entry(card, "SMTP Password:")

        def save():
            db.set_config("manager_name", name_var.get())
            db.set_config("manager_email", email_var.get())
            db.set_config("smtp_server", smtp_server_var.get())
            db.set_config("smtp_port", smtp_port_var.get())
            db.set_config("smtp_user", smtp_user_var.get())
            if smtp_pass_var.get():
                db.set_config("smtp_password", smtp_pass_var.get())
            messagebox.showinfo("Saved", "Configuration saved successfully.")

        self._button(card, "Save Configuration", save, FG_GREEN)

        # Show current config
        self._label(frame, "Current Configuration", font=FONT_SUBHEADING,
                    fg=FG_ACCENT, padx=20, pady=(16, 4))
        config = db.get_all_config()
        if config:
            cfg_card = self._card(frame)
            for key, value in config.items():
                display = "********" if "password" in key else value
                self._label(cfg_card, f"{key}: {display}", fg=FG_DIM)
        else:
            self._label(frame, "  No configuration set yet.", fg=FG_DIM, padx=20)


def main():
    app = ManagerGUI()
    app.mainloop()


if __name__ == "__main__":
    main()

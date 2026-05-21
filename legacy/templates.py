"""
Meeting templates, conversation frameworks, management tips,
coaching intelligence, and wisdom engine.
"""

import os
import re
import random
from datetime import datetime

EVENT_TYPES = {
    "check_in": {"label": "Weekly Check-In", "default_duration": 20, "cadence": "Weekly"},
    "coaching": {"label": "Coaching Session", "default_duration": 45, "cadence": "Bi-weekly or Monthly"},
    "one_on_one": {"label": "1-on-1 Meeting", "default_duration": 30, "cadence": "Weekly or Bi-weekly"},
    "quarterly_review": {"label": "Quarterly Review", "default_duration": 75, "cadence": "Quarterly"},
    "other": {"label": "Other Meeting", "default_duration": 30, "cadence": "As needed"},
}


def get_default_title(event_type, participant_name=None):
    label = EVENT_TYPES.get(event_type, {}).get("label", "Meeting")
    return f"{label}: {participant_name}" if participant_name else label


def generate_agenda(event_type, participant_name=None):
    generators = {
        "check_in": _check_in_agenda, "coaching": _coaching_agenda,
        "one_on_one": _one_on_one_agenda, "quarterly_review": _quarterly_review_agenda,
    }
    gen = generators.get(event_type)
    return gen(participant_name) if gen else "No template available for this event type."


def _check_in_agenda(name=None):
    who = name or "[team member]"
    return f"""WEEKLY CHECK-IN \u2014 {who}
Duration: 15-20 minutes

1. OPEN (2 min)
   - How are things going this week?
   - Listen for energy level and morale signals.

2. PROGRESS (5 min)
   - What did you accomplish since we last talked?
   - What are you most proud of this week?

3. BLOCKERS (5 min)
   - What's slowing you down or getting in your way?
   - Is there anything you need from me or others to move forward?

4. PRIORITIES (3 min)
   - What are your top 2-3 priorities for next week?
   - Confirm alignment with team/org goals.

5. CLOSE (2 min)
   - Summarize action items (yours and theirs).
   - Anything else on your mind?

PRE-MEETING CHECKLIST:
[ ] Reviewed their completed work from the past week
[ ] Checked for any blockers or escalations
[ ] Prepared 1-2 specific recognition points
[ ] Identified organizational updates to share"""


def _coaching_agenda(name=None):
    who = name or "[team member]"
    return f"""COACHING SESSION \u2014 {who}
Duration: 30-45 minutes
Framework: GROW Model

G \u2014 GOAL (5 min)
   - What would you like to focus on today?
   - What does success look like for you in this area?
   - How will you know when you've made progress?

R \u2014 REALITY (10 min)
   - Where are you right now with this skill/goal?
   - What have you tried so far?
   - What's working? What isn't?
   - Walk me through a recent example.

O \u2014 OPTIONS (10 min)
   - What approaches could you take?
   - What would you do if there were no constraints?
   - Who does this well? What do they do differently?
   - What's one small experiment you could try?

W \u2014 WAY FORWARD (10 min)
   - Which option feels most promising?
   - What's your first step?
   - What support do you need?
   - When will you try this by?
   - How should we follow up?

PRE-SESSION CHECKLIST:
[ ] Identified the specific skill or behavior to develop
[ ] Gathered concrete examples (positive and developmental)
[ ] Reviewed progress on previous coaching commitments
[ ] Prepared 2-3 open-ended discovery questions"""


def _one_on_one_agenda(name=None):
    who = name or "[team member]"
    return f"""1-ON-1 MEETING \u2014 {who}
Duration: 30-45 minutes

1. THEIR TOPICS (15 min) \u2014 Always start here
   - What's on your mind?
   - What would be most helpful to discuss today?

2. FEEDBACK EXCHANGE (10 min)
   - Share specific, timely feedback (positive and/or constructive)
   - Use SBI: Situation \u2192 Behavior \u2192 Impact
   - Ask: Is there anything I could be doing differently to support you?

3. CAREER & GROWTH (5-10 min)
   Rotating focus:
   - Week A: Current project satisfaction and challenges
   - Week B: Skill development and learning goals
   - Week C: Career aspirations and next steps
   - Week D: Team dynamics and collaboration

4. ACTION ITEMS (5 min)
   - Summarize commitments from both sides
   - Confirm priorities for the coming week

PRE-MEETING CHECKLIST:
[ ] Reviewed shared 1:1 doc for their agenda items
[ ] Reviewed notes from last 1:1 and open action items
[ ] Checked current project status and risks
[ ] Prepared any feedback to deliver (SBI format)
[ ] Asked yourself: Is there anything I've been avoiding?
[ ] Blocked 5 min before to be mentally present"""


def _quarterly_review_agenda(name=None):
    who = name or "[team member]"
    quarter = _current_quarter()
    return f"""QUARTERLY REVIEW \u2014 {who}
Quarter: {quarter}
Duration: 60-90 minutes

1. SET THE TONE (5 min)
   - This is a conversation, not a verdict.
   - Goal: walk out aligned on where you are and where you're headed.

2. SELF-ASSESSMENT DISCUSSION (15 min)
   - Walk me through how you feel the quarter went.
   - What are you most proud of?
   - Where do you feel you fell short?
   - Listen before sharing your assessment.

3. MANAGER ASSESSMENT (15 min)
   - Goal completion review with evidence
   - Strengths to reinforce (with concrete examples)
   - Development areas (forward-looking, constructive)
   - Where assessments align and differ

4. PEER FEEDBACK (10 min)
   - Share themes (not individual attributions)
   - What colleagues consistently value
   - One area that came up as an opportunity

5. NEXT QUARTER PLANNING (15 min)
   - Propose goals and invite their input
   - Discuss role changes, new responsibilities, stretch assignments
   - Align on development priorities

6. CAREER CHECK-IN (10 min)
   - How are you feeling about your trajectory here?
   - What would make next quarter a great one for you?
   - Longer-term aspirations

7. CLOSE (5 min)
   - Summarize key takeaways
   - Confirm action items
   - How did this conversation feel?

PREPARATION TIMELINE:
  6 weeks before: Communicate review timeline, share self-assessment template
  4 weeks before: Collect peer feedback (3-5 peers per person)
  2 weeks before: Draft performance summary, calibrate ratings
  1 week before:  Finalize written review, prepare next-quarter goal proposals"""


def _current_quarter():
    now = datetime.now()
    q = (now.month - 1) // 3 + 1
    return f"Q{q} {now.year}"


TIPS = [
    "Do what you say you will do. Every time. Consistency builds trust faster than grand gestures.",
    "Be transparent about what you know and don't know. 'I don't have that answer yet, but I'll find out' is a strong statement.",
    "Admit mistakes openly. Your team mirrors your behavior \u2014 model accountability.",
    "Keep confidences. If someone shares something sensitive in a 1:1, protect it absolutely.",
    "In 1:1s, aim for a 30/70 talk ratio \u2014 you 30%, them 70%. Your job is to listen.",
    "Ask 'What else?' after their first answer. The real issue often comes second or third.",
    "Silence is a tool. Let people think. Don't fill every gap with your own words.",
    "When giving context on decisions, explain the 'why' not just the 'what'. People commit to reasons, not instructions.",
    "Don't save feedback for quarterly reviews. By then it's stale and the moment to learn has passed.",
    "Positive feedback should be public and specific. 'Great job' means nothing.",
    "Constructive feedback should be private, timely, and about behavior \u2014 never character.",
    "The best feedback ratio is roughly 5:1 positive to constructive.",
    "Use the SBI framework (Situation, Behavior, Impact) to make feedback clear and non-judgmental.",
    "Every direct report should have a clear development goal at all times.",
    "Create stretch opportunities \u2014 don't wait for them to appear.",
    "Connect people with mentors, sponsors, and visibility beyond your team.",
    "Ask about career goals regularly. They change \u2014 and you should know when they do.",
    "Shield your team from unnecessary meetings, politics, and context-switching. Be the filter.",
    "Say no to low-priority requests so your team can say yes to what matters.",
    "Respect deep work time. Not everything needs an immediate response.",
    "If you wouldn't add this meeting to your own calendar, don't add it to theirs.",
    "Over-communicate context for remote teams. They miss hallway conversations \u2014 be the bridge.",
    "Document decisions. If it's not written down, it didn't happen for distributed team members.",
    "Create informal connection time. Trust is built in the margins, not just the meetings.",
    "Judge output, not online status. Green dots are not a productivity metric.",
    "Block time every week for thinking \u2014 not reacting.",
    "Your mood sets the team's mood. If you're stressed and short, everyone feels it.",
    "The things that feel urgent are rarely the things that matter most.",
    "Ask for feedback on your own management regularly.",
    "Address issues early. The conversation you're avoiding today becomes the crisis you manage next month.",
    "When someone is underperforming, separate the person from the problem.",
    "In conflict, seek to understand before seeking to be understood.",
    "Recognize that most 'performance problems' are actually clarity problems.",
    "Celebrate wins \u2014 especially the quiet ones.",
    "Make space for disagreement. The team that never pushes back isn't aligned \u2014 they're afraid.",
    "Hire for trajectory, not just current skill.",
    "When onboarding someone new, assign them a buddy who isn't you.",
]


def get_random_tip():
    return random.choice(TIPS)

def get_tips_by_count(count=5):
    return random.sample(TIPS, min(count, len(TIPS)))

def get_all_tips():
    return TIPS


ANTI_PATTERNS = [
    {"name": "The Ghost", "symptom": "Cancels 1:1s, slow to respond, unavailable",
     "fix": "Block dedicated time for your team. They are your primary job."},
    {"name": "The Micromanager", "symptom": "Reviews every detail, can't delegate",
     "fix": "Define outcomes, not methods. Check in on results, not process."},
    {"name": "The Buddy", "symptom": "Avoids hard conversations, wants to be liked",
     "fix": "Caring means telling the truth. Kindness is not avoidance."},
    {"name": "The Hero", "symptom": "Takes over when things get hard",
     "fix": "Coach through the problem instead of solving it yourself."},
    {"name": "The Scorekeeper", "symptom": "Remembers every mistake, brings up old issues",
     "fix": "Address issues once, then move forward. Don't keep a ledger."},
    {"name": "The Proxy", "symptom": "Manages through Slack/email, avoids face-to-face",
     "fix": "Sensitive topics require real conversation. Text lacks tone."},
]

TOPIC_BANK = {
    "Engagement & Morale": [
        "What's energizing you about your work right now?",
        "What's draining your energy?",
        "On a scale of 1-10, how would you rate your week? What would make it a point higher?",
        "Is there anything about the team or company that's been on your mind?",
    ],
    "Growth & Career": [
        "What skills do you want to build over the next 6 months?",
        "Is your current work aligned with where you want your career to go?",
        "Who in the company (or industry) do you admire? What about them stands out?",
        "What's a project or role that would be a dream assignment for you?",
    ],
    "Collaboration & Team": [
        "How are things going with your closest collaborators?",
        "Do you feel like you have the right level of autonomy?",
        "What's one thing we could change about how our team works?",
        "Are there meetings or processes that feel like a waste of your time?",
    ],
    "Manager Effectiveness": [
        "What's one thing I could do more of to help you?",
        "What's one thing I should stop doing?",
        "Do you feel like you get enough context on the 'why' behind decisions?",
        "How do you prefer to receive feedback?",
    ],
}

def get_topic_suggestions(category=None):
    if category and category in TOPIC_BANK:
        return {category: TOPIC_BANK[category]}
    return TOPIC_BANK

# ---------------------------------------------------------------------------
# ADDICTIVE DESIGN SKILL — The Habit-Forming Productivity Framework
# Synthesized from 9 books: Hooked (Eyal), Atomic Habits (Clear),
# The Power of Habit (Duhigg), Irresistible (Alter), Nudge (Thaler),
# Influence (Cialdini), Thinking Fast & Slow (Kahneman),
# Predictably Irrational (Ariely), Dopamine Nation (Lembke)
# ---------------------------------------------------------------------------

ADDICTIVE_DESIGN = {
    "hook_model": {
        "name": "The Hook Model (Nir Eyal / B.F. Skinner)",
        "cycle": "Trigger → Action → Variable Reward → Investment",
        "principles": [
            {
                "id": "H1",
                "name": "Internal Triggers Over External",
                "rule": "Associate the app with an emotional state (anxiety about "
                        "falling behind, desire to feel in control) — not just push "
                        "notifications. Long-term retention comes from emotional links.",
                "source": "Hooked (Eyal)",
                "check": "Does opening the dashboard relieve a manager's anxiety?",
            },
            {
                "id": "H2",
                "name": "Reduce Action Friction to Near Zero",
                "rule": "Fogg Behavior Model: B=MAT (Behavior = Motivation × Ability "
                        "× Trigger). Maximize ability by minimizing steps. One-tap, "
                        "pre-filled defaults, instant-open states.",
                "source": "Hooked (Eyal)",
                "check": "Can the user get value in under 10 seconds?",
            },
            {
                "id": "H3",
                "name": "Variable Rewards, Not Predictable Ones",
                "rule": "Skinner's variable ratio reinforcement: rotate reward types "
                        "— sometimes a streak milestone, sometimes a wisdom insight, "
                        "sometimes a pattern discovery. Unpredictability sustains curiosity.",
                "source": "Hooked (Eyal) / B.F. Skinner",
                "check": "Does the daily wisdom change every day? Do coaching provocations vary?",
            },
            {
                "id": "H4",
                "name": "Investment That Loads the Next Trigger",
                "rule": "After reward, ask user to invest — set tomorrow's goal, write "
                        "a note, update a plan. Stored value makes leaving costly and "
                        "pre-loads the next session's trigger.",
                "source": "Hooked (Eyal)",
                "check": "Does every journal save create data that makes tomorrow's "
                         "dashboard smarter?",
            },
        ],
    },
    "atomic_habits": {
        "name": "Four Laws of Behavior Change (James Clear)",
        "cycle": "Cue → Craving → Response → Reward",
        "principles": [
            {
                "id": "A1",
                "name": "Make It Obvious (Cue Visibility)",
                "rule": "Surface the app at decision points. Morning summary, "
                        "calendar integration, home screen widgets. Habit stacking: "
                        "'After I pour my coffee, I review today's dashboard.'",
                "source": "Atomic Habits (Clear)",
                "check": "Is the dashboard useful at a glance without scrolling?",
            },
            {
                "id": "A2",
                "name": "Make It Attractive (Craving Design)",
                "rule": "Temptation bundling — pair productive behavior with something "
                        "pleasant. Visual progress, anticipation of insights, daily "
                        "wisdom reveals.",
                "source": "Atomic Habits (Clear)",
                "check": "Does the user look forward to seeing their daily wisdom and streak?",
            },
            {
                "id": "A3",
                "name": "Make It Easy (Two-Minute Rule)",
                "rule": "Default interaction under 2 minutes. Quick-capture journal, "
                        "one-tap mood, zero required fields. Lower activation energy "
                        "until starting feels effortless.",
                "source": "Atomic Habits (Clear)",
                "check": "Can the user log a journal entry with one sentence and two taps?",
            },
            {
                "id": "A4",
                "name": "Make It Satisfying (Immediate Reward)",
                "rule": "Productivity payoffs are delayed — the app must manufacture "
                        "immediate satisfaction. Completion animations, streak counters, "
                        "wisdom quotes on save, progress bars.",
                "source": "Atomic Habits (Clear)",
                "check": "Does saving a journal entry trigger a matched wisdom reward?",
            },
        ],
    },
    "habit_mechanics": {
        "name": "Habit Mechanics (Duhigg / Alter / Lembke)",
        "principles": [
            {
                "id": "M1",
                "name": "Keystone Habits",
                "rule": "Identify one behavior that cascades into others. Daily "
                        "journaling is the keystone — users who journal daily naturally "
                        "check their dashboard, review team data, and maintain streaks.",
                "source": "The Power of Habit (Duhigg)",
                "check": "Is the Journal the second item in the nav, right after Dashboard?",
            },
            {
                "id": "M2",
                "name": "Goal Gradient Effect",
                "rule": "People accelerate effort as they approach a goal. Show 'X of Y' "
                        "progress indicators, partially-complete weekly targets, onboarding "
                        "checklists that start at 20% done.",
                "source": "Irresistible (Alter)",
                "check": "Does the onboarding checklist start at step 1 of 5 (not 0)?",
            },
            {
                "id": "M3",
                "name": "Cliffhangers and Open Loops (Zeigarnik)",
                "rule": "End sessions with incomplete loops: 'You're 80% to your weekly "
                        "goal', 'Sarah has 2 pending actions.' Unfinished items linger in "
                        "memory, pulling users back.",
                "source": "Irresistible (Alter)",
                "check": "Does the dashboard always show SOMETHING that needs attention?",
            },
            {
                "id": "M4",
                "name": "Intermittent Dosing (Dopamine Balance)",
                "rule": "Space rewards unevenly. Avoid over-rewarding — if every action "
                        "triggers fireworks, users habituate. Keep rewards modest and "
                        "varied. Surprise weekly milestones prevent tolerance buildup.",
                "source": "Dopamine Nation (Lembke)",
                "check": "Do coaching provocations vary each time? Is the wisdom quote "
                         "different daily?",
            },
            {
                "id": "M5",
                "name": "Productive Discomfort",
                "rule": "Tolerating short-term discomfort builds long-term dopamine "
                        "baseline. Acknowledge difficulty: 'This conversation will be "
                        "hard. That's the point.' Reframe effort as investment.",
                "source": "Dopamine Nation (Lembke)",
                "check": "Do anti-pattern alerts feel uncomfortable but useful?",
            },
        ],
    },
    "choice_architecture": {
        "name": "Choice Architecture & Persuasion (Thaler / Cialdini / Kahneman / Ariely)",
        "principles": [
            {
                "id": "C1",
                "name": "Default Effects",
                "rule": "Pre-select the productive option. Auto-fill today's date, "
                        "default mood to 3, pre-generate agendas. What happens when "
                        "the user does nothing is the most impactful design choice.",
                "source": "Nudge (Thaler & Sunstein)",
                "check": "Are all form defaults set to the most useful starting value?",
            },
            {
                "id": "C2",
                "name": "Loss Aversion (2x Multiplier)",
                "rule": "Losses hurt 2x as much as equivalent gains. Frame around "
                        "protecting streaks ('Don't lose your 14-day streak') not "
                        "building them. Show what you'll lose, not what you'll gain.",
                "source": "Thinking Fast and Slow (Kahneman)",
                "check": "Do nudges use loss framing ('18 days without feedback — "
                         "motivation decays') not gain framing?",
            },
            {
                "id": "C3",
                "name": "Peak-End Rule",
                "rule": "Users judge experiences by the peak moment and the ending. "
                        "Every session needs a satisfying peak (completion reward) and "
                        "ends on a positive note (wisdom, not a failure list).",
                "source": "Thinking Fast and Slow (Kahneman)",
                "check": "Does the journal save end with a wisdom quote? Does the "
                         "dashboard end with daily wisdom, not overdue items?",
            },
            {
                "id": "C4",
                "name": "Commitment and Consistency",
                "rule": "Once users complete a small action (first journal, first "
                        "feedback), they feel pressure to remain consistent. Start "
                        "with tiny asks that escalate.",
                "source": "Influence (Cialdini)",
                "check": "Does the onboarding flow start with 'You opened the app' "
                         "(already done) then escalate?",
            },
            {
                "id": "C5",
                "name": "Scarcity of Streaks",
                "rule": "Streaks create something scarce to lose. A 30-day streak "
                        "is irreplaceable — breaking it feels like real loss. This "
                        "drives daily return even when motivation is low.",
                "source": "Influence (Cialdini) / Kahneman",
                "check": "Is the streak counter visible on Dashboard AND Journal?",
            },
            {
                "id": "C6",
                "name": "Social Norms Over Market Norms",
                "rule": "Frame as a personal growth partner, not a transactional "
                        "tool. The moment it feels commercial (aggressive upsells), "
                        "users switch from intrinsic to cost-benefit analysis.",
                "source": "Predictably Irrational (Ariely)",
                "check": "Does the app feel like a coach, not an HR tool?",
            },
            {
                "id": "C7",
                "name": "System 1 Design",
                "rule": "Daily interactions engage fast, automatic System 1 — "
                        "simple icons, binary choices, visual progress. Reserve "
                        "System 2 (analysis) for weekly reviews.",
                "source": "Thinking Fast and Slow (Kahneman)",
                "check": "Is the dashboard scannable in 5 seconds without reading?",
            },
            {
                "id": "C8",
                "name": "Self-Binding (Commitment Devices)",
                "rule": "Give users tools to constrain their future selves: "
                        "auto-scheduled sessions, weekly reflection reminders, "
                        "streaks they can't pause. Digital equivalent of Lembke's "
                        "self-binding strategies.",
                "source": "Dopamine Nation (Lembke) / Nudge (Thaler)",
                "check": "Does the weekly reflection tab create a recurring loop?",
            },
        ],
    },
}


def get_addictive_design_audit():
    """Return a flat list of all addictive design checks for UI audit."""
    checks = []
    for category in ADDICTIVE_DESIGN.values():
        for p in category.get("principles", []):
            checks.append({
                "id": p["id"],
                "name": p["name"],
                "rule": p["rule"],
                "source": p["source"],
                "check": p["check"],
                "category": category["name"],
            })
    return checks


SELF_ASSESSMENT_DIMENSIONS = [
    ("Presence", "Was I available and engaged with my team?"),
    ("Clarity", "Did my team know their priorities and why they matter?"),
    ("Feedback", "Did I give at least one piece of meaningful feedback?"),
    ("Development", "Did I do something to grow someone on my team?"),
    ("Advocacy", "Did I represent my team's needs to leadership?"),
    ("Follow-through", "Did I complete what I committed to?"),
]


# ---------------------------------------------------------------------------
# Wisdom Engine — loads 620+ ideas from the management library
# ---------------------------------------------------------------------------

_WISDOM_CACHE = None
_WISDOM_SECTIONS = None

def _load_wisdom():
    global _WISDOM_CACHE, _WISDOM_SECTIONS
    if _WISDOM_CACHE is not None:
        return _WISDOM_CACHE
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "365_Great_Management_Ideas.md")
    if not os.path.exists(path):
        _WISDOM_CACHE = []
        _WISDOM_SECTIONS = {}
        return _WISDOM_CACHE
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    entries = []
    section_map = {}
    current_section = ""
    current_entry = None
    for line in text.split("\n"):
        if line.startswith("## "):
            current_section = line.lstrip("# ").strip()
        match = re.match(r'^(\d+)\.\s+(.+)', line)
        if match:
            if current_entry:
                entries.append(current_entry)
                section_map.setdefault(current_entry["section"], []).append(
                    len(entries) - 1)
            current_entry = {
                "number": int(match.group(1)),
                "text": match.group(2).strip(),
                "section": current_section,
            }
        elif current_entry and line.strip():
            current_entry["text"] += " " + line.strip()
    if current_entry:
        entries.append(current_entry)
        section_map.setdefault(current_entry["section"], []).append(
            len(entries) - 1)
    _WISDOM_CACHE = entries
    _WISDOM_SECTIONS = section_map
    return _WISDOM_CACHE


def get_daily_wisdom(date=None):
    """Return a deterministic wisdom entry for the given date."""
    if date is None:
        date = datetime.now().date()
    entries = _load_wisdom()
    if not entries:
        return {"number": 0, "text": "No wisdom loaded.", "section": ""}
    idx = date.timetuple().tm_yday % len(entries)
    return entries[idx]


def get_wisdom_from_section(section_keyword):
    """Return a random wisdom entry from a section matching the keyword."""
    _load_wisdom()
    if not _WISDOM_SECTIONS:
        return get_daily_wisdom()
    matches = []
    kw = section_keyword.lower()
    for sec, indices in _WISDOM_SECTIONS.items():
        if kw in sec.lower():
            matches.extend(indices)
    if not matches:
        return random.choice(_WISDOM_CACHE) if _WISDOM_CACHE else get_daily_wisdom()
    return _WISDOM_CACHE[random.choice(matches)]


# Keyword index for wisdom matching
_KEYWORD_INDEX = None

_WISDOM_KEYWORDS = {
    "feedback": ["feedback", "review", "praise", "criticism", "SBI", "performance"],
    "delegation": ["delegate", "delegation", "accountability", "ownership", "autonomy"],
    "meeting": ["meeting", "1-on-1", "one-on-one", "agenda", "check-in"],
    "trust": ["trust", "relationship", "rapport", "safety", "psychological"],
    "politics": ["politics", "political", "influence", "power", "allies", "lateral"],
    "motivation": ["motivation", "engagement", "energy", "morale", "demotivat"],
    "hiring": ["hiring", "interview", "recruit", "onboard", "candidate"],
    "goals": ["goals", "objectives", "OKR", "planning", "strategy", "priorities"],
    "conflict": ["conflict", "difficult", "confrontation", "disagree", "tension"],
    "growth": ["growth", "career", "development", "learning", "mentor", "coaching"],
    "change": ["change", "transformation", "adapt", "transition", "reorg"],
    "sales": ["sales", "selling", "customer", "buying", "negotiat", "value"],
    "boundaries": ["casual", "gossip", "boundaries", "inappropriate", "unprofessional",
                    "too friendly", "crossed a line", "overshared", "vented", "complained",
                    "talked about", "said too much"],
    "rolepower": ["boss", "authority", "role power", "position", "perception",
                   "how they see me", "respect", "credibility", "professional"],
    "ethics": ["ethics", "integrity", "honest", "fair", "unfair", "right thing",
               "moral", "values", "principle", "should I have"],
}


def _build_keyword_index():
    global _KEYWORD_INDEX
    if _KEYWORD_INDEX is not None:
        return _KEYWORD_INDEX
    entries = _load_wisdom()
    _KEYWORD_INDEX = {}
    for i, entry in enumerate(entries):
        text_lower = entry["text"].lower()
        for category, keywords in _WISDOM_KEYWORDS.items():
            for kw in keywords:
                if kw in text_lower:
                    _KEYWORD_INDEX.setdefault(category, []).append(i)
                    break
    return _KEYWORD_INDEX


def match_wisdom_to_text(text, count=1):
    """Match journal text to wisdom entries using keyword scoring.
    Returns top match 70% of the time, random-from-top-10 30% — variable ratio."""
    entries = _load_wisdom()
    if not entries:
        return [get_daily_wisdom()]
    index = _build_keyword_index()
    text_lower = text.lower()
    scores = {}
    for category, keywords in _WISDOM_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower and category in index:
                for idx in index[category]:
                    scores[idx] = scores.get(idx, 0) + 1
    if not scores:
        return random.sample(entries, min(count, len(entries)))
    ranked = sorted(scores, key=scores.get, reverse=True)
    top = ranked[:max(10, count)]
    results = []
    for _ in range(count):
        if random.random() < 0.7 and top:
            results.append(entries[top[0]])
            top = top[1:]
        elif top:
            pick = random.choice(top)
            results.append(entries[pick])
            top = [t for t in top if t != pick]
        else:
            results.append(random.choice(entries))
    return results


# ---------------------------------------------------------------------------
# Coaching Provocations — data-driven prompts for pre-meeting prep
# ---------------------------------------------------------------------------

def get_coaching_provocations(prep_data):
    """Generate coaching provocations from pre-meeting prep data.
    Each provocation pairs a data observation with a wisdom quote.
    Returns list of dicts: {observation, wisdom}"""
    if not prep_data:
        return []
    provocations = []
    name = prep_data.get("member", {}).get("name", "this person")
    pos = prep_data.get("positive_count", 0)
    con = prep_data.get("constructive_count", 0)
    total_fb = pos + con

    if total_fb > 0 and (pos / max(total_fb, 1)) < 0.6:
        w = get_wisdom_from_section("FEEDBACK")
        provocations.append({
            "observation": f"You've given {name} {pos} positive and {con} constructive "
                          f"feedback. Research suggests 5:1 is optimal. What is {name} doing well?",
            "wisdom": w["text"],
        })

    days_fb = prep_data.get("days_since_feedback")
    if days_fb is not None and days_fb > 14:
        w = get_wisdom_from_section("FEEDBACK")
        provocations.append({
            "observation": f"No feedback recorded for {name} in {days_fb} days. "
                          f"Motivation decays without recognition.",
            "wisdom": w["text"],
        })
    elif days_fb is None:
        provocations.append({
            "observation": f"You've never given {name} recorded feedback. "
                          f"What have you observed about their work?",
            "wisdom": get_wisdom_from_section("FEEDBACK")["text"],
        })

    days_mtg = prep_data.get("days_since_meeting")
    if days_mtg is not None and days_mtg > 14:
        w = get_wisdom_from_section("MEETING")
        provocations.append({
            "observation": f"It's been {days_mtg} days since your last conversation "
                          f"with {name}.",
            "wisdom": w["text"],
        })

    pending = prep_data.get("pending_actions", 0)
    if pending > 2:
        w = get_wisdom_from_section("DELEGATION")
        provocations.append({
            "observation": f"{name} has {pending} pending action items. "
                          f"Are they blocked, or do these need revisiting?",
            "wisdom": w["text"],
        })

    for g in prep_data.get("active_goals", []):
        if g.get("status") == "not_started":
            w = get_wisdom_from_section("GOALS")
            provocations.append({
                "observation": f"{name}'s goal \"{g['description'][:60]}\" hasn't "
                              f"started yet. Is this still the right goal?",
                "wisdom": w["text"],
            })
            break

    return provocations


# ---------------------------------------------------------------------------
# Anti-Pattern Detector — watches YOUR behavior
# ---------------------------------------------------------------------------

def detect_anti_patterns(meeting_data, feedback_ratios, manager_name=None):
    """Detect personal management anti-patterns from analytics data.
    Returns list of {pattern, evidence, suggestion, wisdom}."""
    patterns = []

    # The Ghost: not meeting enough
    if meeting_data:
        for m in meeting_data:
            days = m.get("days_since")
            if days is not None and days > 21:
                w = get_wisdom_from_section("MEETING")
                patterns.append({
                    "pattern": "The Ghost",
                    "evidence": f"It's been {days} days since you met with "
                               f"{m.get('member_name', 'a team member')}.",
                    "suggestion": "Block dedicated time. Your team is your primary job.",
                    "wisdom": w["text"],
                })
                break
            if days is None:
                patterns.append({
                    "pattern": "The Ghost",
                    "evidence": f"You've never had a recorded meeting with "
                               f"{m.get('member_name', 'a team member')}.",
                    "suggestion": "Schedule your first 1-on-1 this week.",
                    "wisdom": get_wisdom_from_section("MEETING")["text"],
                })
                break

    # The Micromanager / The Buddy / The Scorekeeper — from feedback ratios
    if feedback_ratios:
        total_pos = sum(r.get("positive_count", 0) for r in feedback_ratios)
        total_con = sum(r.get("constructive_count", 0) for r in feedback_ratios)
        total = total_pos + total_con
        if total > 3 and total_con / max(total, 1) > 0.8:
            patterns.append({
                "pattern": "The Micromanager",
                "evidence": f"Your feedback is {int(total_con/max(total,1)*100)}% "
                           f"constructive across all members.",
                "suggestion": "What is your team doing well? Catch them doing something right.",
                "wisdom": get_wisdom_from_section("FEEDBACK")["text"],
            })
        if total > 0 and total_con == 0:
            patterns.append({
                "pattern": "The Buddy",
                "evidence": "You haven't given any constructive feedback recently.",
                "suggestion": "Caring means telling the truth. Kindness is not avoidance.",
                "wisdom": get_wisdom_from_section("FEEDBACK")["text"],
            })
        for r in feedback_ratios:
            if r.get("constructive_count", 0) > 3 and r.get("positive_count", 0) == 0:
                patterns.append({
                    "pattern": "The Scorekeeper",
                    "evidence": f"{r.get('member_name', 'Someone')} has received "
                               f"only constructive feedback and zero positive.",
                    "suggestion": "Address issues once, then move forward. What are their strengths?",
                    "wisdom": get_wisdom_from_section("FEEDBACK")["text"],
                })
                break

    return patterns

"""
Meeting templates, conversation frameworks, and management tips.
Provides structured agendas and guidance for each event type.
"""

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

SELF_ASSESSMENT_DIMENSIONS = [
    ("Presence", "Was I available and engaged with my team?"),
    ("Clarity", "Did my team know their priorities and why they matter?"),
    ("Feedback", "Did I give at least one piece of meaningful feedback?"),
    ("Development", "Did I do something to grow someone on my team?"),
    ("Advocacy", "Did I represent my team's needs to leadership?"),
    ("Follow-through", "Did I complete what I committed to?"),
]

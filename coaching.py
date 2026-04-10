"""
AI Coaching Engine — Claude-powered management coaching sidebar.

Generates contextual questions, provocations, and devil's advocate
challenges based on the user's notes and the 23-book management
wisdom library. Acts as a brainstorming partner, reminder, prompter,
and coach.
"""

import os
import database as db
import templates

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

# ---------------------------------------------------------------------------
# Book wisdom context — curated excerpts by theme for the system prompt
# ---------------------------------------------------------------------------

COACHING_CONTEXT = """You are an expert management coach embedded in a private manager's tool.
You have deep knowledge of these management books and their key ideas:

FROM HIGH OUTPUT MANAGEMENT (Andy Grove):
- A manager's output = the output of their organization + neighboring orgs under influence
- Only two ways to improve employee output: motivate and train
- Task-relevant maturity determines management style (structured → communicating → monitoring)
- Reports are self-discipline, not communication. Planning is the end, not the bound volume.
- Let chaos reign, then rein in chaos. Detect problems at lowest-value stage.
- Performance reviews are the highest-leverage activity. Assess performance, not potential.

FROM FIRST, BREAK ALL THE RULES (Buckingham & Coffman):
- People don't change that much. Draw out what was left in, don't put in what was left out.
- Great managers capitalize on differences, not grind them down.
- You can't infer excellence from studying failure. Average is the anomaly.
- Every role performed at excellence deserves respect. Every role has its own nobility.
- The catalyst role: speed up the reaction between talent and company goals.

FROM THE EFFECTIVE MANAGER (Horstman):
- Results AND retention define an effective manager.
- Relationship with directs is 40% of total management value.
- Your directs see you as the boss, not as a nice person. Role power distorts every interaction.
- Weekly 30-minute one-on-ones are the single most important management behavior.
- Don't rush to negative feedback. Build trust first for 12 weeks.

FROM 100 TRUTHS / BEST PRACTICES FOR OPERATING EXCELLENCE (Dellanna):
- Trust incentives rather than people. Consistency is the most important attribute.
- Delegate results, not methods. Prescribing methods removes accountability.
- The manager's role is not to motivate but to avoid demotivation — prevent motivational losses.
- Management debt: sacrificing clarity, fairness, or consistency to avoid a difficult conversation.
- Every time an employee achieves an objective but doesn't get rewarded, motivation dies.

FROM SCALING PEOPLE (Claire Hughes Johnson):
- Leadership is disappointing people at a rate they can absorb.
- Build self-awareness to build mutual awareness. Say the thing you think you cannot say.
- Performance = results x behaviors. It's multiplicative.
- Your primary goal is to work yourself out of a job.
- Give new leaders data points, not judgments. Let them form their own conclusions.

FROM HBR GUIDES (Office Politics, Professional Growth, Critical Thinking, Leading Through Change):
- 85% of C-suite executives admit their orgs are bad at problem diagnosis.
- Managing your career is 100% your responsibility. Be wary of conventional wisdom.
- Most decisions should be made with about 70% of the information you want.
- The most powerful predictor of virality/attention is anger, not truth.
- Very few people rise without allies. You didn't build any bridges.

FROM TRUST ME, I'M LYING (Ryan Holiday):
- What begins online ends offline. What rules the media rules the country.
- The most powerful predictor of what spreads is anger. Reasonableness doesn't spread.
- Things must be negative but not too negative. Hopelessness drives inaction; anger drives sharing.

FROM THE ALGORITHM (McNeill):
- Question every requirement. What appeared as requirements were often just recommendations.
- A process cannot go faster than its slowest step. Hunt for bottlenecks.
- A corporate culture expands its possibilities if it looks at every 'no' as a potential 'yes.'

FROM GAME THEORY (Pfeiffer):
- To make an optimal decision, ask what you would do if you were the other player.
- A deterministic strategy can be easily exploited. Unpredictability creates immunity.
- The maximin strategy optimizes the worst-case scenario.

FROM VALUE-BASED FEES (Weiss) / SLOW DOWN SELL FASTER (Davis):
- Fees are about value, not time. Manage the value up, not the fee down.
- Traditional selling is any sales process not in sync with the psychology of buying.
- Customers award the prize to whoever was there through every step of their buying process.
"""

SYSTEM_PROMPT = COACHING_CONTEXT + """
YOUR ROLE:
You are the manager's private thinking partner. When they share notes about
a meeting, event, observation, or diary entry, you respond with:

1. PROBING QUESTIONS — Ask 2-3 sharp questions that help them think deeper.
   Not generic. Based on what they wrote. Challenge their assumptions.

2. FRAMEWORK APPLICATION — Pick the most relevant framework from the books
   above and show how it applies to their specific situation.

3. DEVIL'S ADVOCATE — Offer one counterpoint or perspective they probably
   haven't considered. Push back constructively.

4. ACTION PROMPT — Suggest one concrete next step they could take.

RULES:
- Be direct and concise. No fluff. No corporate-speak.
- Reference specific books/authors when relevant (e.g., "Grove would say...")
- If the situation involves a difficult conversation, help them rehearse it.
- If it involves a decision, help them think through second-order effects.
- If it involves a person, remind them to consider that person's perspective.
- Ask uncomfortable questions when needed. You're a coach, not a cheerleader.
- Keep total response under 250 words. Density over length.
- Use markdown formatting for readability.
"""


def _get_client():
    """Get an Anthropic client using the stored API key."""
    if Anthropic is None:
        return None
    api_key = db.get_config("anthropic_api_key")
    if not api_key:
        return None
    return Anthropic(api_key=api_key)


def _build_context(notes, context_type="journal", member_name=None,
                   event_type=None, prep_data=None):
    """Build a context-rich user message for Claude."""
    parts = [f"CONTEXT TYPE: {context_type}"]

    if member_name:
        parts.append(f"TEAM MEMBER: {member_name}")

    if event_type:
        parts.append(f"EVENT TYPE: {event_type}")

    if prep_data:
        days_m = prep_data.get("days_since_meeting")
        days_f = prep_data.get("days_since_feedback")
        pos = prep_data.get("positive_count", 0)
        con = prep_data.get("constructive_count", 0)
        pending = prep_data.get("pending_actions", 0)
        goals = prep_data.get("active_goals", [])
        parts.append(f"DATA: Last meeting {days_m} days ago. "
                     f"Last feedback {days_f} days ago. "
                     f"Feedback ratio: {pos} positive / {con} constructive. "
                     f"Pending actions: {pending}. "
                     f"Active goals: {len(goals)}.")
        if goals:
            parts.append("Goals: " + "; ".join(
                g["description"][:60] for g in goals[:3]))

    # Add a relevant wisdom quote for additional grounding
    if notes:
        matched = templates.match_wisdom_to_text(notes, count=1)
        if matched:
            parts.append(f"RELEVANT WISDOM: {matched[0]['text'][:200]}")

    parts.append(f"MY NOTES:\n{notes}")

    return "\n".join(parts)


def get_coaching_response(notes, context_type="journal", member_name=None,
                          event_type=None, prep_data=None):
    """Call Claude to generate coaching questions and provocations.

    Returns the coaching response as a string, or an error/fallback message.
    """
    if not notes or not notes.strip():
        return None

    client = _get_client()
    if client is None:
        # Fallback: use the local wisdom matcher instead of API
        return _local_fallback(notes, context_type, member_name)

    user_message = _build_context(
        notes, context_type, member_name, event_type, prep_data)

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        return message.content[0].text
    except Exception as e:
        return f"*Coaching unavailable: {e}*\n\n" + _local_fallback(
            notes, context_type, member_name)


def _local_fallback(notes, context_type="journal", member_name=None):
    """Offline fallback when no API key is configured.
    Uses the wisdom matcher + template-based provocations."""
    parts = []

    # Match wisdom to notes
    matched = templates.match_wisdom_to_text(notes, count=2)
    if matched:
        parts.append("**Relevant wisdom from your library:**")
        for m in matched:
            parts.append(f"> {m['text']}")
        parts.append("")

    # Generate template-based questions based on context type
    questions = _generate_template_questions(notes, context_type, member_name)
    if questions:
        parts.append("**Questions to consider:**")
        for q in questions:
            parts.append(f"- {q}")

    return "\n".join(parts) if parts else None


def _generate_template_questions(notes, context_type, member_name=None):
    """Generate relevant questions without AI, based on keyword detection."""
    questions = []
    text = notes.lower()
    name = member_name or "this person"

    # Situation-specific questions
    if any(w in text for w in ["frustrated", "angry", "annoyed", "upset"]):
        questions.append(f"What is {name}'s perspective on this situation?")
        questions.append("Is this a pattern, or a one-time event? What evidence do you have?")
        questions.append("What would Grove say — is this a capability issue or a motivation issue?")

    if any(w in text for w in ["performance", "underperform", "struggling", "behind"]):
        questions.append(f"Have you set unambiguous expectations with {name}? "
                        "Would they describe the same gap you see?")
        questions.append("Dellanna asks: are you delegating results or methods?")
        questions.append("What's the task-relevant maturity here — do they need structure, "
                        "communication, or just monitoring?")

    if any(w in text for w in ["promote", "promotion", "career", "growth", "develop"]):
        questions.append(f"Is {name} scaling to the call, or comfortable at the current level?")
        questions.append("Buckingham says: are you trying to put in what was left out, "
                        "or draw out what was left in?")
        questions.append("What would the next 6 months look like if they were truly stretched?")

    if any(w in text for w in ["feedback", "conversation", "tell them", "address"]):
        questions.append("Have you practiced what you'll actually say? "
                        "Can you frame it using SBI — Situation, Behavior, Impact?")
        questions.append("Grove: 'Don't confuse emotional comfort with operational need.' "
                        "What outcome do you need from this conversation?")
        questions.append(f"What does {name} think they're doing well right now?")

    if any(w in text for w in ["meeting", "1-on-1", "one-on-one", "check-in"]):
        questions.append(f"What does {name} need from you right now — "
                        "a manager or a leader?")
        questions.append("Are you doing 80% of the talking, or are they?")
        questions.append("What's the one thing you're avoiding bringing up?")

    if any(w in text for w in ["politics", "boss", "executive", "leadership", "influence"]):
        questions.append("Who are your allies on this? Have you socialized the idea "
                        "before the meeting?")
        questions.append("Kaplan warns: 'Otherwise confident executives overestimate "
                        "the risk of speaking up and underestimate the risk of staying silent.'")
        questions.append("What's the second-order effect if you do nothing?")

    if any(w in text for w in ["delegate", "trust", "let go", "handoff", "empower"]):
        questions.append("Are you delegating the result or the method? "
                        "If the method, you haven't actually delegated.")
        questions.append(f"What's the worst that happens if {name} does it differently than you would?")
        questions.append("Johnson says: your primary goal is to work yourself out of a job. "
                        "What are you still holding onto?")

    if any(w in text for w in ["hire", "interview", "candidate", "recruit"]):
        questions.append("Buckingham: what specific talents does this role require? "
                        "Not the job title — the actual talents.")
        questions.append("Grove: 'Careful interviewing doesn't guarantee anything, "
                        "it merely increases your odds of getting lucky.'")
        questions.append("Are you hiring for the role as it is today, or as it will be in 6 months?")

    if any(w in text for w in ["conflict", "disagree", "tension", "difficult"]):
        questions.append("Have you named the conflict explicitly? 'Try to make the implicit, explicit.'")
        questions.append("Is this a difference of agendas, perceptions, or personal styles?")
        questions.append("What would it look like to approach this person as an ally, not an adversary?")

    if any(w in text for w in ["change", "reorg", "transition", "new"]):
        questions.append("Frei: 'Comfortable inaction is riskier than uncomfortable action.' "
                        "What's the cost of not now?")
        questions.append("Have your people shifted from 'difficult, costly, weird' to "
                        "'easy, rewarding, normal' yet?")
        questions.append("What isn't changing? Clarifying that can be very reassuring.")

    # Default questions if nothing matched
    if not questions:
        questions.append("What are you really trying to accomplish here?")
        questions.append("What would you advise a friend in this exact situation?")
        questions.append("What's the thing you're not saying out loud?")

    return questions[:4]  # Max 4 questions

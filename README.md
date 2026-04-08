# Manager Tool

A private management coaching journal that makes you a better manager. Not another HR tool — a thinking partner that connects your reflections to your team data and a 620-idea management wisdom library.

## What Makes This Different

| Generic HR Tool | This Tool |
|---|---|
| Built for HR to monitor managers | Built for the manager to develop themselves |
| Empty forms | Coaching provocations + wisdom at the right moment |
| Public by default | Private by default |
| Tracks compliance | Detects your personal anti-patterns |
| Separates learning from workflow | Embeds 620 ideas INTO the workflow |
| 10-minute sessions | 30-second sessions |

## Core Features

### Journal (the core product)
- Zero-friction daily entries — no required fields, just write
- Mood/energy tracking with trend visualization
- Weekly self-assessment (6 dimensions: Presence, Clarity, Feedback, Development, Advocacy, Follow-through)
- Journal streak tracking with loss-aversion mechanics
- On save: matched wisdom quote from the 620-idea library (variable reward)

### AI Coaching Sidebar
- Right-pane coaching on Journal, Events, Feedback, and Member Timeline pages
- **With API key**: Claude-powered coaching — probing questions, framework application, devil's advocate, action prompts — grounded in 23 management books
- **Without API key**: Local fallback using keyword-matched wisdom + situation-specific question templates
- References specific authors: "Grove would say...", "Buckingham asks...", "Dellanna warns..."

### Dashboard (30-second value)
- Daily wisdom (different every day — variable reward)
- Nudges: "It's been 18 days since you met with Sarah" (loss-framed triggers)
- Anti-pattern alerts: "You're showing signs of The Ghost" (identity hook)
- Streak counters, quick stats, onboarding checklist

### Member Timeline + Pre-Meeting Prep
- Before your 1-on-1: days since last meeting, feedback ratio, pending actions, active goals
- Coaching provocations generated from live team data + wisdom library
- Chronological activity feed (events, feedback, goals, actions, career conversations)

### Analytics + Anti-Pattern Detection
- Personal anti-pattern detector (The Ghost, The Micromanager, The Buddy, The Scorekeeper)
- Meeting cadence per member, feedback health ratios, goal completion rates
- Self-assessment trends over time
- Management score

### Career Development
- Career conversation tracker (separate from 1-on-1s)
- Skills inventory with proficiency levels and strength/growth flags
- Development plans with milestones

### Team Management
- Team roster with member detail views
- Event scheduling (check-ins, coaching, 1-on-1s, quarterly reviews)
- SBI framework feedback (Situation, Behavior, Impact)
- Quarterly goal tracking with OKR-style key results
- Action item tracking with overdue warnings

### Manager Profiles
- Individual login with username/password
- Work schedule configuration (days, hours, timezone)
- Profile management

## Architecture

| File | Lines | Purpose |
|---|---|---|
| `web_app.py` | ~1,335 | Streamlit web application — all pages and UI |
| `database.py` | ~1,304 | SQLite database layer — 7 original + 6 new tables, ~50 functions |
| `templates.py` | ~821 | Wisdom engine, coaching provocations, anti-pattern detector, meeting agendas, 21-principle addictive design framework |
| `coaching.py` | ~296 | Claude API integration for contextual management coaching |
| `calendar_service.py` | ~168 | iCalendar (.ics) generation and SMTP email |
| `365_Great_Management_Ideas.md` | ~1,400 | 620 management ideas from 23 books |
| `manager_tool.py` | ~892 | CLI interface |
| `gui.py` | ~832 | Tkinter desktop GUI |

## Database Schema

**Manager data**: managers (profiles + auth)

**Team data**: team_members, events, action_items, feedback, goals

**Private journal**: journal_entries, self_assessments

**Career**: career_conversations, skills, development_plans, milestones

**System**: config

All tables with `manager_id` FK for multi-user isolation.

## The Wisdom Library

620 management ideas extracted from 23 books:

- High Output Management (Andy Grove)
- First, Break All the Rules (Buckingham & Coffman)
- The Effective Manager (Mark Horstman)
- Scaling People (Claire Hughes Johnson)
- 100 Truths You Will Learn Too Late (Luca Dellanna)
- Best Practices for Operating Excellence (Luca Dellanna)
- The New One Minute Manager (Ken Blanchard)
- Trust Me, I'm Lying (Ryan Holiday)
- Accountability Everywhere (Tate, Pantalon & David)
- The Algorithm (Jonathan McNeill)
- Game Theory (Christoph Pfeiffer)
- Slow Down, Sell Faster (Kevin Davis)
- Value-Based Fees (Alan Weiss)
- HBR Guide to Critical Thinking
- HBR Guide to Office Politics
- HBR Guide to Your Professional Growth
- HBR Guide to Leading Through Change
- HBR Guide to Making Better Decisions
- HBR's 10 Must Reads
- And others

## Addictive Design Framework

21 behavioral psychology principles from 9 books (Hooked, Atomic Habits, The Power of Habit, Irresistible, Nudge, Influence, Thinking Fast & Slow, Predictably Irrational, Dopamine Nation) are built into the UI:

- **Hook Model**: Trigger → Action → Variable Reward → Investment
- **Four Laws**: Make it Obvious, Attractive, Easy, Satisfying
- **Loss Aversion**: Streaks, loss-framed nudges
- **Variable Ratio Reinforcement**: Daily wisdom, coaching provocations
- **Zeigarnik Effect**: Dashboard always shows open loops
- **Peak-End Rule**: Sessions end with wisdom, not failure lists

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the web app
streamlit run web_app.py

# Or run the CLI
python manager_tool.py

# Or run the desktop GUI
python manager_tool.py --gui
```

## Configuration

1. **Create an account** — username, password, work schedule
2. **Add your team** — name, role, email
3. **Start journaling** — the core habit. Even one sentence counts.
4. **Optional: Add Anthropic API key** in Settings for Claude-powered coaching

## Deployment

The app is designed for Streamlit Community Cloud:

1. Push to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your repo, set `web_app.py` as the main file
4. Deploy

Note: SQLite works for single-user / demo. For production multi-user, migrate to PostgreSQL.

## License

Private repository.

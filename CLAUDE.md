# CLAUDE.md — Project Intelligence

## Project Overview
Manager Tool is a Streamlit-based management coaching journal with a dual-mode database (SQLite local / PostgreSQL Supabase). The primary interface is `web_app.py`. The wisdom library contains 620 management ideas from 23 books.

## Development Commands
```bash
# Run locally
streamlit run web_app.py

# Run tests
python -m pytest tests/ -v

# Verify syntax
python -c "import py_compile; py_compile.compile('web_app.py', doraise=True)"
```

## Key Architecture Decisions
- All database functions use `_exec()`/`_fetchone()`/`_fetchall()` helpers for PostgreSQL placeholder conversion (`?` → `%s`)
- All user-owned data is filtered by `manager_id` for multi-tenancy
- Date columns are TEXT (YYYY-MM-DD) — SQL helpers cast to `::text` for PostgreSQL comparison
- Passwords use bcrypt; sensitive config values use Fernet encryption
- Connection pooling via `_PooledConnection` wrapper (transparent to callers)

---

## Skills

---
name: code-validator
description: Expert code quality and error-checking skill. Activates on requests to "review code", "check for bugs", "debug", or "fix errors".
allowed-tools: [Read, Grep, LS]
---

# Code Validation & Error Checking

## 1. Triggering Context
Use this skill whenever you are asked to review code, troubleshoot a failure, or before finalizing any significant code change.

## 2. Mandatory Validation Checklist
Before declaring a task "fixed," you MUST verify:
- **Silent Failures:** Search for `try-except` blocks without logging or re-raising. Flag any instance where an error is "swallowed".
- **Import Accuracy:** Verify that all newly added imports actually exist in the project environment.
- **Edge Cases:** Explicitly check for null/undefined inputs, empty lists, and network timeouts.

## 3. Troubleshooting & Recovery
If a tool call or validation fails, follow this recovery table:

| Problem | Immediate Action |
|---------|------------------|
| Tool Timeout | Wait 5s and retry once. If it fails again, report the specific timeout to the user. |
| Missing Context | Use `Grep` to find where the variable/class is defined before guessing its structure. |
| Test Failure | Do NOT "fix" the test to pass. Analyze the logic error in the source code first. |

## 4. Critical Rules
- **No Hallucinations:** If an MCP tool returns "no results," do NOT generate synthetic data. State "No results found" and ask for alternative search parameters.
- **Opinionated Naming:** Flag "clever" or ambiguous variable names (e.g., `temp`, `data`, `handle`). Suggest explicit alternatives.
- **Test-First Debugging:** Always suggest a specific test case that would have caught the bug being fixed.

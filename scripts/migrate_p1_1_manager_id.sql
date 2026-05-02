-- Migration P1.1: add manager_id to orphan child tables and backfill from parents.
-- Idempotent: safe to re-run. Run via: psql "$DATABASE_URL" -f scripts/migrate_p1_1_manager_id.sql
--
-- Background: AUDIT.md C2 — feedback, goals, career_conversations, skills,
-- development_plans, and milestones lacked a manager_id column. Without it,
-- IDOR scoping requires joining through team_members on every read/write
-- (P1.2). Adding the column lets future queries filter directly.

BEGIN;

ALTER TABLE feedback              ADD COLUMN IF NOT EXISTS manager_id INTEGER REFERENCES managers(id);
ALTER TABLE goals                 ADD COLUMN IF NOT EXISTS manager_id INTEGER REFERENCES managers(id);
ALTER TABLE career_conversations  ADD COLUMN IF NOT EXISTS manager_id INTEGER REFERENCES managers(id);
ALTER TABLE skills                ADD COLUMN IF NOT EXISTS manager_id INTEGER REFERENCES managers(id);
ALTER TABLE development_plans     ADD COLUMN IF NOT EXISTS manager_id INTEGER REFERENCES managers(id);
ALTER TABLE milestones            ADD COLUMN IF NOT EXISTS manager_id INTEGER REFERENCES managers(id);

-- Backfill from parent. Tables that hang off team_members:
UPDATE feedback             SET manager_id = tm.manager_id FROM team_members tm WHERE feedback.team_member_id = tm.id            AND feedback.manager_id IS NULL;
UPDATE goals                SET manager_id = tm.manager_id FROM team_members tm WHERE goals.team_member_id = tm.id               AND goals.manager_id IS NULL;
UPDATE career_conversations SET manager_id = tm.manager_id FROM team_members tm WHERE career_conversations.team_member_id = tm.id AND career_conversations.manager_id IS NULL;
UPDATE skills               SET manager_id = tm.manager_id FROM team_members tm WHERE skills.team_member_id = tm.id              AND skills.manager_id IS NULL;
UPDATE development_plans    SET manager_id = tm.manager_id FROM team_members tm WHERE development_plans.team_member_id = tm.id   AND development_plans.manager_id IS NULL;

-- milestones piggyback on development_plans (must run AFTER development_plans backfill above)
UPDATE milestones SET manager_id = dp.manager_id FROM development_plans dp WHERE milestones.plan_id = dp.id AND milestones.manager_id IS NULL;

-- Sanity: report any rows that are still NULL after backfill (orphaned data).
-- These are rows whose parent team_member or development_plan no longer exists.
DO $$
DECLARE
    cnt INTEGER;
BEGIN
    SELECT
        (SELECT COUNT(*) FROM feedback             WHERE manager_id IS NULL) +
        (SELECT COUNT(*) FROM goals                WHERE manager_id IS NULL) +
        (SELECT COUNT(*) FROM career_conversations WHERE manager_id IS NULL) +
        (SELECT COUNT(*) FROM skills               WHERE manager_id IS NULL) +
        (SELECT COUNT(*) FROM development_plans    WHERE manager_id IS NULL) +
        (SELECT COUNT(*) FROM milestones           WHERE manager_id IS NULL)
        INTO cnt;
    IF cnt > 0 THEN
        RAISE NOTICE 'P1.1 backfill: % orphan rows remain (parent team_member or development_plan missing). Investigate before P1.2.', cnt;
    END IF;
END $$;

COMMIT;

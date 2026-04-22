-- Run this after pg_restore --data-only into Neon
-- Resets all SERIAL sequences to match the actual max IDs in each table

SELECT setval(pg_get_serial_sequence('managers', 'id'), COALESCE(MAX(id), 1)) FROM managers;
SELECT setval(pg_get_serial_sequence('team_members', 'id'), COALESCE(MAX(id), 1)) FROM team_members;
SELECT setval(pg_get_serial_sequence('events', 'id'), COALESCE(MAX(id), 1)) FROM events;
SELECT setval(pg_get_serial_sequence('action_items', 'id'), COALESCE(MAX(id), 1)) FROM action_items;
SELECT setval(pg_get_serial_sequence('feedback', 'id'), COALESCE(MAX(id), 1)) FROM feedback;
SELECT setval(pg_get_serial_sequence('goals', 'id'), COALESCE(MAX(id), 1)) FROM goals;
SELECT setval(pg_get_serial_sequence('journal_entries', 'id'), COALESCE(MAX(id), 1)) FROM journal_entries;
SELECT setval(pg_get_serial_sequence('self_assessments', 'id'), COALESCE(MAX(id), 1)) FROM self_assessments;
SELECT setval(pg_get_serial_sequence('career_conversations', 'id'), COALESCE(MAX(id), 1)) FROM career_conversations;
SELECT setval(pg_get_serial_sequence('skills', 'id'), COALESCE(MAX(id), 1)) FROM skills;
SELECT setval(pg_get_serial_sequence('development_plans', 'id'), COALESCE(MAX(id), 1)) FROM development_plans;
SELECT setval(pg_get_serial_sequence('milestones', 'id'), COALESCE(MAX(id), 1)) FROM milestones;
SELECT setval(pg_get_serial_sequence('users', 'id'), COALESCE(MAX(id), 1)) FROM users;
SELECT setval(pg_get_serial_sequence('delegations', 'id'), COALESCE(MAX(id), 1)) FROM delegations;
SELECT setval(pg_get_serial_sequence('running_notes', 'id'), COALESCE(MAX(id), 1)) FROM running_notes;
SELECT setval(pg_get_serial_sequence('decisions', 'id'), COALESCE(MAX(id), 1)) FROM decisions;
SELECT setval(pg_get_serial_sequence('coach_suggestions', 'id'), COALESCE(MAX(id), 1)) FROM coach_suggestions;

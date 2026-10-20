-- 008: full agent answer on the run (progress.result). Untrusted agent text, never logged. Additive.
BEGIN;
ALTER TABLE agent_runs ADD COLUMN result text;
COMMIT;

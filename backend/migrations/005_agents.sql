-- 005: coding agent per runner/run (chosen by the Jev router or the runner default). Additive.
BEGIN;
ALTER TABLE runners ADD COLUMN agents jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE agent_runs ADD COLUMN agent jsonb;
COMMIT;

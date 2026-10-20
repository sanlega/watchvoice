BEGIN;
DROP TABLE user_controls; DROP TABLE pairing_codes; DROP TABLE policy_rules;
ALTER TABLE approvals DROP CONSTRAINT approvals_run_required;
ALTER TABLE approvals DROP COLUMN instruction_id, DROP COLUMN payload, DROP COLUMN risk, DROP COLUMN kind;
ALTER TABLE approvals ALTER COLUMN run_id SET NOT NULL; -- fails if policy_change rows exist
ALTER TABLE agent_runs DROP COLUMN stale_notified_at, DROP COLUMN deadline_at, DROP COLUMN runtime_mode;
ALTER TABLE runners DROP COLUMN trust;
ALTER TABLE devices DROP COLUMN apns_env, DROP COLUMN apns_token, DROP COLUMN public_key;
-- instruction_status 'applied' cannot be dropped from a PG enum; harmless.
COMMIT;

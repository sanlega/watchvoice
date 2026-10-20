BEGIN;
ALTER TABLE outbox_events DROP COLUMN last_error, DROP COLUMN attempts;
ALTER TABLE agent_runs DROP COLUMN last_event_at, DROP COLUMN external_ref, DROP COLUMN summary,
  DROP COLUMN callback_expires_at, DROP COLUMN callback_token_hash, DROP COLUMN runner_id;
ALTER TABLE instructions DROP COLUMN runner_hint;
DROP TABLE runners;
COMMIT;

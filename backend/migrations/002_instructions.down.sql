-- Rollback of 002. Destroys instruction data: only for environments without real data.
BEGIN;
ALTER TABLE tasks DROP COLUMN instruction_id;
ALTER TABLE routing_decisions DROP CONSTRAINT routing_source_xor;
ALTER TABLE routing_decisions DROP COLUMN instruction_id;
-- recording_id NOT NULL is restored only if no instruction-sourced rows remain.
ALTER TABLE routing_decisions ALTER COLUMN recording_id SET NOT NULL;
DROP TABLE instruction_events;
DROP TABLE instructions;
ALTER TABLE devices DROP COLUMN scopes;
DROP TYPE instruction_status;
COMMIT;

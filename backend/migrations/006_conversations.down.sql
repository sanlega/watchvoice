BEGIN;
ALTER TABLE instructions DROP COLUMN reply_to_run_id;
COMMIT;

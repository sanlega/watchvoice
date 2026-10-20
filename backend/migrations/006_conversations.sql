-- 006: replies continue an existing T3 conversation (run.message.v1). Additive.
BEGIN;
-- No FK on purpose: an unknown or foreign run must become needs_clarification, not a 500 (like project_hint).
ALTER TABLE instructions ADD COLUMN reply_to_run_id uuid;
COMMIT;

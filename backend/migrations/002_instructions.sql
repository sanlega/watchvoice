-- 002: transcript-first instructions (Apple Watch). Additive and non-destructive over
-- docs/vox-agent-spec/schema.sql (v0.2, treated as 001). No column is dropped or
-- retyped; the only relaxation is routing_decisions.recording_id NOT NULL, guarded
-- by a CHECK that every pre-existing row already satisfies.
-- Rollback: see 002_instructions.down.sql (only valid before any instruction rows exist).
BEGIN;

CREATE TYPE instruction_status AS ENUM
  ('queued','routing','needs_clarification','captured','dispatched','awaiting_approval','failed','cancelled');

-- Watches reuse `devices` (hardware_model = 'apple_watch'); scopes keep ESP32 audio-only.
ALTER TABLE devices ADD COLUMN scopes jsonb NOT NULL DEFAULT '["recordings:write"]'::jsonb;

CREATE TABLE instructions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 user_id uuid NOT NULL REFERENCES users(id),
 device_id uuid REFERENCES devices(id),
 client_instruction_id uuid NOT NULL,
 source text NOT NULL CHECK (source IN ('apple_watch','web','esp32_text')),
 text text NOT NULL CHECK (char_length(text) BETWEEN 1 AND 4000), -- TODO(W-046): encrypt at rest
 language text,
 project_hint uuid,
 transcription_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
 request_digest char(64) NOT NULL,
 status instruction_status NOT NULL DEFAULT 'queued',
 intent text,
 project_id uuid REFERENCES projects(id),
 task_id uuid, agent_run_id uuid, approval_id uuid,
 candidates jsonb NOT NULL DEFAULT '[]'::jsonb,
 error_code text,
 version integer NOT NULL DEFAULT 1,
 trace_id uuid NOT NULL DEFAULT gen_random_uuid(),
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (user_id, client_instruction_id)
);
CREATE INDEX idx_instructions_user_created ON instructions(user_id, created_at DESC);

CREATE TABLE instruction_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 instruction_id uuid NOT NULL REFERENCES instructions(id),
 event_type text NOT NULL,
 safe_payload jsonb NOT NULL DEFAULT '{}'::jsonb, -- never transcript text
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_instruction_events ON instruction_events(instruction_id, created_at);

ALTER TABLE routing_decisions ALTER COLUMN recording_id DROP NOT NULL;
ALTER TABLE routing_decisions ADD COLUMN instruction_id uuid UNIQUE REFERENCES instructions(id);
ALTER TABLE routing_decisions ADD CONSTRAINT routing_source_xor
  CHECK ((recording_id IS NULL) <> (instruction_id IS NULL));

ALTER TABLE tasks ADD COLUMN instruction_id uuid UNIQUE REFERENCES instructions(id);

COMMIT;

-- 003: execution on T3 Code machines (VPS/PC/Mac) via signed webhooks. Additive only.
BEGIN;
CREATE TABLE runners (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 user_id uuid NOT NULL REFERENCES users(id),
 name text NOT NULL,
 aliases jsonb NOT NULL DEFAULT '[]'::jsonb,
 webhook_url text NOT NULL CHECK (webhook_url LIKE 'https://%'),
 secret_env text NOT NULL, -- env var NAME holding the HMAC secret; the secret never touches the DB
 is_default boolean NOT NULL DEFAULT false,
 enabled boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (user_id, name)
);
CREATE UNIQUE INDEX one_default_runner_per_user ON runners(user_id) WHERE is_default;

ALTER TABLE instructions ADD COLUMN runner_hint text;
ALTER TABLE agent_runs
  ADD COLUMN runner_id uuid REFERENCES runners(id),
  ADD COLUMN callback_token_hash char(64),        -- reserved; tokens are HMAC-derived today
  ADD COLUMN callback_expires_at timestamptz,
  ADD COLUMN summary text,                         -- agent-written, untrusted display text
  ADD COLUMN external_ref text,                    -- T3 Code thread id
  ADD COLUMN last_event_at timestamptz;
ALTER TABLE outbox_events
  ADD COLUMN attempts integer NOT NULL DEFAULT 0,
  ADD COLUMN last_error text;
COMMIT;

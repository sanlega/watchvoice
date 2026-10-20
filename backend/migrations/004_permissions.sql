-- 004: ADR-0001 execution permissions, device-signed approvals, push, pause. Additive.
BEGIN;
ALTER TYPE instruction_status ADD VALUE IF NOT EXISTS 'applied';
ALTER TABLE devices ADD COLUMN public_key text, ADD COLUMN apns_token text, ADD COLUMN apns_env text;
ALTER TABLE runners ADD COLUMN trust text NOT NULL DEFAULT 'isolated' CHECK (trust IN ('isolated','cooperative'));
ALTER TABLE agent_runs ADD COLUMN runtime_mode text, ADD COLUMN deadline_at timestamptz,
  ADD COLUMN stale_notified_at timestamptz;

ALTER TABLE approvals ALTER COLUMN run_id DROP NOT NULL;
ALTER TABLE approvals
  ADD COLUMN kind text NOT NULL DEFAULT 'action' CHECK (kind IN ('action','policy_change','agent_request')),
  ADD COLUMN risk text NOT NULL DEFAULT 'high' CHECK (risk IN ('low','medium','high')),
  ADD COLUMN payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN instruction_id uuid REFERENCES instructions(id);
ALTER TABLE approvals ADD CONSTRAINT approvals_run_required CHECK (kind = 'policy_change' OR run_id IS NOT NULL);

CREATE TABLE policy_rules (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 user_id uuid NOT NULL REFERENCES users(id),
 runner_id uuid REFERENCES runners(id),
 project_id uuid REFERENCES projects(id),
 mode text NOT NULL CHECK (mode IN ('bypass','granular')),
 effects jsonb NOT NULL DEFAULT '{}'::jsonb,
 source text NOT NULL CHECK (source IN ('cli','voice')),
 expires_at timestamptz, revoked_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_policy_rules_user ON policy_rules(user_id) WHERE revoked_at IS NULL;

CREATE TABLE pairing_codes (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 user_id uuid NOT NULL REFERENCES users(id),
 code_hash char(64) NOT NULL UNIQUE,
 name text NOT NULL,
 replace_device_id uuid REFERENCES devices(id),
 expires_at timestamptz NOT NULL, used_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE user_controls (user_id uuid PRIMARY KEY REFERENCES users(id), paused_at timestamptz);
COMMIT;

-- VOX Agent relational schema v0.2 — PostgreSQL 16+
-- Use application migrations; this is baseline conceptual executable DDL.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TYPE recording_state AS ENUM ('pending_upload','stored','transcribing','transcribed','routing','routed','needs_review','failed');
CREATE TYPE task_state AS ENUM ('draft','queued','running','waiting_approval','completed','failed','cancelled');
CREATE TYPE run_state AS ENUM ('queued','provisioning','running','waiting_approval','succeeded','failed','cancelled','timed_out');
CREATE TYPE approval_state AS ENUM ('pending','approved','denied','expired','cancelled');
CREATE TYPE effect_state AS ENUM ('pending','applied','failed','needs_reconciliation');

CREATE TABLE users (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), email text NOT NULL UNIQUE,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE devices (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id),
 name text NOT NULL, hardware_model text NOT NULL, firmware_version text,
 credential_hash text NOT NULL, revoked_at timestamptz, last_seen_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE projects (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id),
 slug text NOT NULL, name text NOT NULL, description text NOT NULL DEFAULT '',
 repo_url text, default_branch text NOT NULL DEFAULT 'main',
 context_json jsonb NOT NULL DEFAULT '{}'::jsonb, archived_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id,slug), UNIQUE(id,user_id)
);
CREATE TABLE project_aliases (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 alias text NOT NULL, alias_norm text NOT NULL,
 UNIQUE(project_id,alias_norm)
);
CREATE TABLE recordings (
 id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id), device_id uuid REFERENCES devices(id),
 state recording_state NOT NULL DEFAULT 'pending_upload', object_key text,
 sha256_hex char(64), mime_type text, bytes bigint CHECK(bytes IS NULL OR bytes > 0),
 duration_ms integer CHECK(duration_ms IS NULL OR duration_ms BETWEEN 1 AND 90000),
 language text, transcript text, stt_provider text, stt_model text,
 error_code text, trace_id uuid NOT NULL DEFAULT gen_random_uuid(),
 created_at timestamptz NOT NULL DEFAULT now(), received_at timestamptz,
 UNIQUE(id,user_id)
);
CREATE TABLE routing_decisions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), recording_id uuid NOT NULL UNIQUE REFERENCES recordings(id),
 user_id uuid NOT NULL REFERENCES users(id), project_id uuid REFERENCES projects(id),
 intent text NOT NULL, proposed_project_name text, task_title text,
 agent_type text, confidence numeric(5,4) CHECK(confidence BETWEEN 0 AND 1),
 candidates jsonb NOT NULL DEFAULT '[]', reasoning_summary text,
 model_version text NOT NULL, needs_review boolean NOT NULL DEFAULT false,
 corrected_by uuid REFERENCES users(id), corrected_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE tasks (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id),
 project_id uuid NOT NULL, recording_id uuid REFERENCES recordings(id),
 parent_task_id uuid REFERENCES tasks(id), title text NOT NULL, description text NOT NULL DEFAULT '',
 intent text NOT NULL, agent_type text NOT NULL, state task_state NOT NULL DEFAULT 'draft',
 execution_mode text NOT NULL DEFAULT 'full', budget_usd numeric(12,4) NOT NULL DEFAULT 2 CHECK(budget_usd >= 0),
 trace_id uuid NOT NULL DEFAULT gen_random_uuid(), created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(project_id,user_id) REFERENCES projects(id,user_id),
 UNIQUE(id,user_id), UNIQUE(recording_id)
);
CREATE TABLE agent_runs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id),
 task_id uuid NOT NULL, attempt integer NOT NULL CHECK(attempt > 0),
 state run_state NOT NULL DEFAULT 'queued', executor text NOT NULL,
 branch_name text, artifact_prefix text, external_pr_url text,
 started_at timestamptz, finished_at timestamptz, exit_reason text,
 billed_usd numeric(12,4) NOT NULL DEFAULT 0 CHECK(billed_usd >= 0),
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(task_id,user_id) REFERENCES tasks(id,user_id), UNIQUE(task_id,attempt)
);
CREATE TABLE capability_grants (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id),
 project_id uuid REFERENCES projects(id), capability text NOT NULL,
 scope_json jsonb NOT NULL DEFAULT '{}'::jsonb,
 max_cost_usd numeric(12,4), expires_at timestamptz,
 granted_by uuid NOT NULL REFERENCES users(id), revoked_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE approvals (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id),
 run_id uuid NOT NULL REFERENCES agent_runs(id), capability text NOT NULL,
 action_summary text NOT NULL, action_digest char(64) NOT NULL,
 state approval_state NOT NULL DEFAULT 'pending',
 expires_at timestamptz NOT NULL, decided_by uuid REFERENCES users(id),
 decided_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE external_effects (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), run_id uuid NOT NULL REFERENCES agent_runs(id),
 effect_key text NOT NULL UNIQUE, capability text NOT NULL, request_digest char(64) NOT NULL,
 state effect_state NOT NULL DEFAULT 'pending', provider_reference text,
 updated_at timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE audit_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 user_id uuid REFERENCES users(id), run_id uuid REFERENCES agent_runs(id),
 actor_type text NOT NULL, actor_id text NOT NULL, event_type text NOT NULL,
 subject_type text NOT NULL, subject_id text NOT NULL,
 payload jsonb NOT NULL DEFAULT '{}'::jsonb, trace_id uuid,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE outbox_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), event_type text NOT NULL,
 aggregate_id uuid NOT NULL, trace_id uuid NOT NULL,
 payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 published_at timestamptz
);
CREATE TABLE api_idempotency (
 user_id uuid NOT NULL REFERENCES users(id), endpoint text NOT NULL, key text NOT NULL,
 request_digest char(64) NOT NULL, status_code integer NOT NULL,
 response_json jsonb NOT NULL, expires_at timestamptz NOT NULL,
 PRIMARY KEY(user_id,endpoint,key)
);
CREATE INDEX idx_recordings_user_created ON recordings(user_id,created_at DESC);
CREATE INDEX idx_projects_user ON projects(user_id,archived_at);
CREATE INDEX idx_tasks_project_created ON tasks(project_id,created_at DESC);
CREATE INDEX idx_tasks_state ON tasks(state,created_at);
CREATE INDEX idx_runs_task ON agent_runs(task_id,attempt DESC);
CREATE INDEX idx_approvals_state ON approvals(user_id,state,created_at DESC);
CREATE INDEX idx_outbox_unpublished ON outbox_events(created_at) WHERE published_at IS NULL;
CREATE INDEX idx_audit_trace ON audit_events(trace_id,created_at);
CREATE INDEX idx_alias_norm ON project_aliases(alias_norm);
-- Hardening requirement: service authorization must check ownership and capability.
-- Multi-tenant expansion: apply PostgreSQL RLS for user-owned tables with a
-- SECURITY DEFINER-safe policy design and `SET LOCAL app.user_id` per transaction.
-- No RLS assumed by this baseline! The deployment must not expose DB directly.

"""Tables used by the API. Mirrors docs/vox-agent-spec/schema.sql (v0.2) plus
backend/migrations/002_instructions.sql. On PostgreSQL the SQL files are the
source of truth (enums are text here so the same code runs on SQLite in tests)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, MetaData,
                        Numeric, String, Table, Text, UniqueConstraint, Uuid, create_engine, event)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
Json = JSON().with_variant(JSONB(), "postgresql")


def now() -> datetime:
    return datetime.now(timezone.utc)


def pk():
    return Column("id", Uuid, primary_key=True, default=uuid.uuid4)


def ts(name, **kw):
    return Column(name, DateTime(timezone=True), **kw)


users = Table("users", metadata, pk(), Column("email", Text, nullable=False, unique=True),
              ts("created_at", nullable=False, default=now))

devices = Table(
    "devices", metadata, pk(),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False),
    Column("name", Text, nullable=False), Column("hardware_model", Text, nullable=False),
    Column("firmware_version", Text), Column("credential_hash", Text, nullable=False),
    # 002: scopes; default keeps ESP32 devices audio-only.
    Column("scopes", JSON, nullable=False, default=lambda: ["recordings:write"]),
    ts("revoked_at"), ts("last_seen_at"), ts("created_at", nullable=False, default=now))

projects = Table(
    "projects", metadata, pk(),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False),
    Column("slug", Text, nullable=False), Column("name", Text, nullable=False),
    Column("description", Text, nullable=False, default=""), Column("repo_url", Text),
    Column("default_branch", Text, nullable=False, default="main"),
    Column("context_json", Json, nullable=False, default=dict), ts("archived_at"),
    ts("created_at", nullable=False, default=now), ts("updated_at", nullable=False, default=now),
    UniqueConstraint("user_id", "slug"))

project_aliases = Table(
    "project_aliases", metadata, pk(),
    Column("project_id", Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    Column("alias", Text, nullable=False), Column("alias_norm", Text, nullable=False),
    UniqueConstraint("project_id", "alias_norm"))

instructions = Table(
    "instructions", metadata, pk(),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False),
    Column("device_id", Uuid, ForeignKey("devices.id")),
    Column("client_instruction_id", Uuid, nullable=False),
    Column("source", Text, nullable=False), Column("text", Text, nullable=False),
    Column("language", Text), Column("project_hint", Uuid), Column("runner_hint", Text),  # 003
    Column("transcription_metadata", Json, nullable=False, default=dict),
    Column("request_digest", String(64), nullable=False),
    Column("status", Text, nullable=False, default="queued"), Column("intent", Text),
    Column("project_id", Uuid, ForeignKey("projects.id")),
    Column("task_id", Uuid), Column("agent_run_id", Uuid), Column("approval_id", Uuid),
    Column("candidates", Json, nullable=False, default=list), Column("error_code", Text),
    Column("version", Integer, nullable=False, default=1),
    Column("trace_id", Uuid, nullable=False, default=uuid.uuid4),
    ts("created_at", nullable=False, default=now), ts("updated_at", nullable=False, default=now),
    UniqueConstraint("user_id", "client_instruction_id"))

instruction_events = Table(
    "instruction_events", metadata, pk(),
    Column("instruction_id", Uuid, ForeignKey("instructions.id"), nullable=False),
    Column("event_type", Text, nullable=False), Column("safe_payload", Json, nullable=False, default=dict),
    ts("created_at", nullable=False, default=now))

routing_decisions = Table(
    "routing_decisions", metadata, pk(),
    Column("recording_id", Uuid),  # 002: nullable; exactly one of recording_id/instruction_id
    Column("instruction_id", Uuid, ForeignKey("instructions.id"), unique=True),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False),
    Column("project_id", Uuid, ForeignKey("projects.id")),
    Column("intent", Text, nullable=False), Column("proposed_project_name", Text),
    Column("task_title", Text), Column("agent_type", Text), Column("confidence", Numeric(5, 4)),
    Column("candidates", Json, nullable=False, default=list), Column("reasoning_summary", Text),
    Column("model_version", Text, nullable=False), Column("needs_review", Boolean, nullable=False, default=False),
    ts("created_at", nullable=False, default=now),
    CheckConstraint("(recording_id IS NULL) <> (instruction_id IS NULL)", name="routing_source_xor"))

tasks = Table(
    "tasks", metadata, pk(),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False),
    Column("project_id", Uuid, ForeignKey("projects.id"), nullable=False),
    Column("recording_id", Uuid, unique=True),
    Column("instruction_id", Uuid, ForeignKey("instructions.id"), unique=True),  # 002
    Column("parent_task_id", Uuid), Column("title", Text, nullable=False),
    Column("description", Text, nullable=False, default=""), Column("intent", Text, nullable=False),
    Column("agent_type", Text, nullable=False), Column("state", Text, nullable=False, default="draft"),
    Column("execution_mode", Text, nullable=False, default="full"),
    Column("budget_usd", Numeric(12, 4), nullable=False, default=2),
    Column("trace_id", Uuid, nullable=False, default=uuid.uuid4),
    ts("created_at", nullable=False, default=now), ts("updated_at", nullable=False, default=now))

runners = Table(  # 003: machines running T3 Code (VPS, PC, Mac) reachable through a signed webhook
    "runners", metadata, pk(),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False),
    Column("name", Text, nullable=False), Column("aliases", JSON, nullable=False, default=list),
    Column("webhook_url", Text, nullable=False),
    Column("secret_env", Text, nullable=False),  # name of the env var holding the HMAC secret, never the secret
    Column("is_default", Boolean, nullable=False, default=False),
    Column("enabled", Boolean, nullable=False, default=True),
    ts("created_at", nullable=False, default=now), UniqueConstraint("user_id", "name"))

agent_runs = Table(
    "agent_runs", metadata, pk(),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False),
    Column("task_id", Uuid, ForeignKey("tasks.id"), nullable=False),
    Column("attempt", Integer, nullable=False), Column("state", Text, nullable=False, default="queued"),
    Column("executor", Text, nullable=False), Column("external_pr_url", Text),
    # 003: T3 Code execution tracking
    Column("runner_id", Uuid, ForeignKey("runners.id")), Column("callback_token_hash", String(64)),
    ts("callback_expires_at"), Column("summary", Text), Column("external_ref", Text), ts("last_event_at"),
    ts("created_at", nullable=False, default=now), UniqueConstraint("task_id", "attempt"))

capability_grants = Table(
    "capability_grants", metadata, pk(),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False),
    Column("project_id", Uuid, ForeignKey("projects.id")), Column("capability", Text, nullable=False),
    Column("scope_json", Json, nullable=False, default=dict), Column("max_cost_usd", Numeric(12, 4)),
    ts("expires_at"), Column("granted_by", Uuid, ForeignKey("users.id"), nullable=False),
    ts("revoked_at"), ts("created_at", nullable=False, default=now))

approvals = Table(
    "approvals", metadata, pk(),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False),
    Column("run_id", Uuid, ForeignKey("agent_runs.id"), nullable=False),
    Column("capability", Text, nullable=False), Column("action_summary", Text, nullable=False),
    Column("action_digest", String(64), nullable=False),
    Column("state", Text, nullable=False, default="pending"),
    ts("expires_at", nullable=False), Column("decided_by", Uuid, ForeignKey("users.id")), ts("decided_at"),
    ts("created_at", nullable=False, default=now))

approval_challenges = Table(  # 002
    "approval_challenges", metadata, pk(),
    Column("approval_id", Uuid, ForeignKey("approvals.id"), nullable=False),
    Column("action_fingerprint", String(64), nullable=False),
    Column("nonce_hash", String(64), nullable=False, unique=True),
    # shortcut: token kept so the owner can fetch it from GET /approvals; replace with
    # a per-device encrypted delivery when step-up auth lands (W-044).
    Column("token", Text, nullable=False),
    ts("expires_at", nullable=False), ts("consumed_at"), Column("principal_id", Uuid),
    ts("created_at", nullable=False, default=now))

audit_events = Table(
    "audit_events", metadata, Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Uuid), Column("run_id", Uuid),
    Column("actor_type", Text, nullable=False), Column("actor_id", Text, nullable=False),
    Column("event_type", Text, nullable=False), Column("subject_type", Text, nullable=False),
    Column("subject_id", Text, nullable=False), Column("payload", Json, nullable=False, default=dict),
    Column("trace_id", Uuid), ts("created_at", nullable=False, default=now))

outbox_events = Table(
    "outbox_events", metadata, pk(),
    Column("event_type", Text, nullable=False), Column("aggregate_id", Uuid, nullable=False),
    Column("trace_id", Uuid, nullable=False), Column("payload", Json, nullable=False),
    Column("attempts", Integer, nullable=False, default=0), Column("last_error", Text),  # 003
    ts("created_at", nullable=False, default=now), ts("published_at"))

api_idempotency = Table(
    "api_idempotency", metadata,
    Column("user_id", Uuid, ForeignKey("users.id"), primary_key=True),
    Column("endpoint", Text, primary_key=True), Column("key", Text, primary_key=True),
    Column("request_digest", String(64), nullable=False), Column("status_code", Integer, nullable=False),
    Column("response_json", Json, nullable=False), ts("expires_at", nullable=False))


def make_engine(url: str):
    engine = create_engine(url, future=True)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return engine

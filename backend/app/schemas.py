"""Pydantic models for contracts/openapi.yaml v0.3.0 (Instructions, Approvals)."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Strict = ConfigDict(extra="forbid")  # unknown fields (e.g. "execution_mode", "policy") are rejected


class Transcription(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    engine: Literal["whistle", "system_dictation", "remote", "manual", "mock"]
    model_version: str | None = Field(default=None, max_length=128)
    on_device: bool
    duration_ms: int | None = Field(default=None, ge=0, le=600_000)


class ClientInfo(BaseModel):
    model_config = Strict
    app_version: str = Field(max_length=32)
    platform: Literal["watchos", "web", "ios", "esp32"]


class NewInstruction(BaseModel):
    model_config = Strict
    client_instruction_id: UUID
    source: Literal["apple_watch", "web", "esp32_text"]
    text: str = Field(min_length=1, max_length=4000)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    project_hint: UUID | None = None
    runner_hint: str | None = Field(default=None, min_length=1, max_length=64)  # v0.3: "mac", "vps", ...
    reply_to_run_id: UUID | None = None  # v0.6: answer inside that run's conversation instead of a new session
    transcription: Transcription
    client: ClientInfo


InstructionStatus = Literal["queued", "routing", "needs_clarification", "captured", "dispatched",
                            "awaiting_approval", "applied", "failed", "cancelled"]


class InstructionReceipt(BaseModel):
    instruction_id: UUID
    status: InstructionStatus
    created_at: datetime
    links: dict[str, str]


class Candidate(BaseModel):
    project_id: UUID
    name: str


class Instruction(BaseModel):
    id: UUID
    client_instruction_id: UUID
    status: InstructionStatus
    intent: str | None
    project_id: UUID | None
    task_id: UUID | None
    agent_run_id: UUID | None
    approval_id: UUID | None
    candidates: list[Candidate]
    error_code: str | None
    progress: "RunProgress | None" = None
    created_at: datetime
    updated_at: datetime


class RunProgress(BaseModel):
    runner: str | None
    agent: str | None = None  # key of the coding agent, e.g. "claude", "codex"
    run_state: str
    summary: str | None
    result: str | None = None  # v0.8: full agent answer, untrusted display text
    pr_url: str | None
    updated_at: datetime | None


class RunEventIn(BaseModel):
    """Callback from the T3 Code run. Can only move the run forward; never touches policy."""
    model_config = Strict
    event: Literal["started", "progress", "needs_input", "succeeded", "failed"]
    summary: str | None = Field(default=None, max_length=500)
    result: str | None = Field(default=None, max_length=4000)  # v0.8: full answer; never logged/audited
    pr_url: str | None = Field(default=None, max_length=500, pattern=r"^https://")
    external_ref: str | None = Field(default=None, max_length=200)  # T3 thread id


class Approval(BaseModel):
    id: UUID
    kind: Literal["action", "policy_change", "agent_request"]
    run_id: UUID | None
    capability: str
    action_summary: str
    agent_provided: bool  # summary written by the agent: untrusted display text
    state: str
    risk: Literal["low", "medium", "high"]
    expires_at: datetime
    action_fingerprint: str  # sign this (ADR-0001 §5); not secret
    project_id: UUID | None = None
    runner: str | None = None
    runner_trust: Literal["isolated", "cooperative"] | None = None


class SignedBody(BaseModel):
    model_config = Strict
    ts: str = Field(max_length=40)
    signature: str = Field(max_length=200)  # base64 DER ECDSA P-256 / SHA-256


class ApprovalDecision(SignedBody):
    decision: Literal["approved", "denied"]
    action_fingerprint: str = Field(min_length=64, max_length=64)


class PauseIn(SignedBody):
    paused: bool


class PairIn(BaseModel):
    model_config = Strict
    code: str = Field(min_length=8, max_length=8)
    public_key: str = Field(max_length=200)  # base64 X9.63 P-256 public key
    name: str = Field(min_length=1, max_length=64)


class PairOut(BaseModel):
    device_id: UUID
    token: str


class PushTokenIn(BaseModel):
    model_config = Strict
    token: str = Field(pattern=r"^[0-9a-fA-F]{32,200}$")
    environment: Literal["production", "development"]


class AgentApprovalIn(BaseModel):
    model_config = Strict
    capability: str = Field(min_length=1, max_length=64)
    action_summary: str = Field(min_length=1, max_length=500)
    details: dict = Field(default_factory=dict)


class AgentApprovalOut(BaseModel):
    approval_id: UUID | None
    state: Literal["pending", "approved", "denied", "expired"]
    expires_at: datetime | None


class PolicyRuleOut(BaseModel):
    id: UUID
    runner: str | None
    project: str | None
    mode: Literal["bypass", "granular"]
    effects: dict[str, str]
    source: str
    expires_at: datetime | None


Instruction.model_rebuild()

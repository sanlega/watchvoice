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
    transcription: Transcription
    client: ClientInfo


InstructionStatus = Literal["queued", "routing", "needs_clarification", "captured", "dispatched",
                            "awaiting_approval", "failed", "cancelled"]


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
    run_state: str
    summary: str | None
    pr_url: str | None
    updated_at: datetime | None


class RunEventIn(BaseModel):
    """Callback from the T3 Code run. Can only move the run forward; never touches policy."""
    model_config = Strict
    event: Literal["started", "progress", "needs_input", "succeeded", "failed"]
    summary: str | None = Field(default=None, max_length=500)
    pr_url: str | None = Field(default=None, max_length=500, pattern=r"^https://")
    external_ref: str | None = Field(default=None, max_length=200)  # T3 thread id


class Approval(BaseModel):
    id: UUID
    run_id: UUID
    capability: str
    action_summary: str
    state: str
    expires_at: datetime
    project_id: UUID | None = None
    risk: Literal["low", "medium", "high"] = "high"
    challenge_token: str | None = None


class ApprovalDecision(BaseModel):
    model_config = Strict
    decision: Literal["approved", "denied"]
    challenge_token: str | None = None


Instruction.model_rebuild()

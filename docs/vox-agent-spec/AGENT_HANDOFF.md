# Agent handoff protocol

## Shared goal
Deliver VOX Agent's end-to-end vertical slice as defined in `PRD.md` without conflicting interface assumptions. Code changes only within assigned ownership area unless a reviewed RFC authorizes cross-cutting edits.

## Contract freeze
- OpenAPI: `openapi.yaml` is canonical external API specification.
- DB: `schema.sql` is canonical target model; migrations are generated, reviewed and applied by Backend owner.
- Events: envelope + event names from PRD §7; any consumer must tolerate unknown fields.
- Use UTC, UUIDs and structured JSON errors. Never silently invent an API field or endpoint.
- Type clients from OpenAPI, do not handwrite duplicated models.
- Each agent creates a branch `agent/<stream>/<ticket-id>` and submits a PR with tests and screenshots/logs as appropriate.

## Recommended repo layout
```text
vox-agent/
  contracts/openapi.yaml
  contracts/events/
  docs/PRD.md
  docs/adr/
  migrations/
  services/api/
  services/intelligence/
  services/policy/
  services/executor/
  firmware/
  apps/dashboard/
  infra/
  tests/contract/
  tests/e2e/
```

## Shared communication
Before work: state ticket ID, chosen branch, dependencies, contract assumptions. While coding: only modify owned paths. Upon completion: publish exact commit/PR, test commands, outputs, API changes, open questions. Block integration if tests disagree with contract; do not patch around mismatches.

## Mandatory integration fixtures
1. Spanish explicit project: “En Musiquillas añade modo multijugador.” → `existing_project`.
2. New project: “Crea un proyecto llamado Ideas Locas...” → `new_project`.
3. Capture only: “Anota esta idea, no la ejecutes.” → no run.
4. Ambiguous continuation: “Sigue con eso de ayer.” → manual review.
5. Privileged: “Publica a producción.” → policy check and approval/grant.
6. Retried same recording ID/audio digest → only one downstream task.
7. Retried ID/different digest → HTTP 409.
8. External effect timeout → `needs_reconciliation`, not duplicate action.

## Agent-specific kickoff prompts
**Architect:** Validate OpenAPI 3.1, PostgreSQL constraints, state machines and event envelope. Produce migration/test contract PR; do not implement business services.

**Backend:** Implement API per OpenAPI and SQL under your owned directories. Include authenticated device recording ingestion, idempotent operations, outbox atomicity and permission checks.

**Intelligence:** Implement STT adapter and hybrid router. Build labeled fixture suite, abstention pathway and strict JSON output validation. Do not call agent executors directly.

**Security:** Implement capability grant evaluation, approvals, request digest binding, tool authorization API and audit. Default-deny unknown capabilities.

**Executor:** Implement isolated agent job runner and Git adapter with worktree, per-run branch and PR creation. Request authorization for every tool execution through policy broker. Never mount host credentials or Docker socket.

**Firmware:** Build ESP-IDF push-to-talk capture and durable recording queue. Integrate cellular modem + TLS after using a mocked HTTPS receiver. Preserve same recording ID and digest for every retry.

**Frontend:** Implement dashboard from generated API client and mock server first. Include routing correction and scoped approval UX, never treat upload ACK as task completion.

**Platform/QA:** Build secure Compose baseline, CI, queue/outbox, integration harness and field-testing runbook. Inject transient faults and verify deduplication and recovery.

## First architecture review blockers
- Decide STT provider and cost cap.
- Decide executor provider & sandbox launcher contract.
- Confirm SIM operator bands/APN and LTE module hardware guide.
- Resolve standing grant authorization policy for production deploys.
- Add RLS/security hardening before multiuser release.

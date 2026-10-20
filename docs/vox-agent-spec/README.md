# VOX Agent — Engineering Specification v0.2

**Date:** 2026-10-19  
**Decisions:** extended autonomous execution (`full`) · hybrid routing · self-hosted backend · ESP32-S3 + LTE Cat 1 bis.

## Deliverables
- `PRD.md`: product requirements, architecture, state machines, requirements, nonfunctional targets, rollout, risks.
- `schema.sql`: PostgreSQL 16+ schema and constraints.
- `openapi.yaml`: OpenAPI 3.1 contract for devices, recordings, projects, tasks, decisions, agents, approvals.
- `BACKLOG.md`: tickets, ownership, dependencies, acceptance criteria, parallel work streams.
- `AGENT_HANDOFF.md`: instructions and interface freeze for independent coding agents.

## Architectural principles
1. Device captures and reliably delivers voice, never holds LLM/API provider secrets.
2. Classification is a proposal; policy engine is authoritative for execution permissions.
3. A task is durable; a run is an individual attempt. Retry does not duplicate tasks or side effects.
4. Independent work streams share versioned API/schema contracts and contract tests.
5. Privileged actions are governed by scoped, time-limited grants and approval gates.

## First vertical slice
Press button → record → LTE HTTP upload → durable receipt → STT → hybrid route → durable task → constrained agent run → visible outcome.

## Build order
Contract freeze (schema, OpenAPI, events) → parallel service mocks and firmware → vertical integration → reliability/security → field tests.

## Local bootstrap (intended; implementation not included)
`docker compose up -d postgres redis minio api worker dashboard`.

## Notes
These files are implementation specifications, not completed source code. Before purchasing a modem, validate carrier frequency bands, SIM APN, power-supply transient requirements, and certification for the target country.

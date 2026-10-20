# VOX Agent — Initial implementation backlog

## Parallel execution strategy
**M0 (contract gate):** A0-01, A0-02, A0-03 must merge first. Then API, firmware, router, agents, dashboard can proceed from mocks and fixtures in parallel. Never modify shared contracts without an RFC and version bump.

**Priority:** P0 blocks vertical slice; P1 before field beta; P2 later.

| Ticket | Priority | Owner | Depends on | Deliverable / acceptance criteria |
|---|---|---|---|---|
| A0-01 | P0 | Architect | — | Freeze OpenAPI 3.1 schemas, error codes, idempotency semantics; run schema validation; 3 sample requests/responses. |
| A0-02 | P0 | Architect/DB | — | Review `schema.sql`; migration 001; test create/drop and FK constraints in ephemeral Postgres. |
| A0-03 | P0 | Architect | A0-01 | Define event envelope, names, fixtures, canonical recording UUID and digest; add consumer contract tests. |
| API-01 | P0 | Backend | A0-01,A0-02 | FastAPI skeleton, typed settings, auth, health, trace IDs, API docs; `pytest` green. |
| API-02 | P0 | Backend | API-01,A0-03 | Binary upload endpoint, digest/size validation, durable storage+DB transaction/outbox, replay 200 / mismatch 409 tests. |
| API-03 | P0 | Backend | API-01,A0-02 | Projects and aliases CRUD read/create; ownership and slug collision tests. |
| API-04 | P0 | Backend | API-01,A0-02 | Task/run transitions validated; idempotent mutators; audit trail; invalid-transition 409. |
| Q-01 | P0 | Platform | A0-03,A0-02 | Transactional outbox relay, Redis worker retries with DLQ, duplicate event test. |
| STT-01 | P0 | AI | A0-03,API-02 | Provider interface `transcribe(recording_ref, glossary)`; mock+real adapter; metadata persist; provider failure retry tests. |
| ROUTE-01 | P0 | AI | A0-01,API-03,STT-01 | Explicit/alias recognizer, candidate retrieval; golden Spanish fixtures. |
| ROUTE-02 | P0 | AI | ROUTE-01 | Structured LLM router with schema validator and abstain branch; 100 annotated test prompts and report. |
| POLICY-01 | P0 | Security | A0-02 | Capability grant evaluator, scope/expiry/budget decisions; allow/deny matrix tests. |
| POLICY-02 | P0 | Security | POLICY-01 | Approval state machine; exact action digest binding, expiry, re-check at execution time. |
| AGENT-01 | P0 | Agents | API-04,POLICY-01 | Agent runner interface + fake executor; isolated job lifecycle, timeout/cancel, results. |
| AGENT-02 | P0 | Agents | AGENT-01,POLICY-02 | Git agent adapter: isolated worktree, restricted credentials, tests, branch + draft PR; policy broker. |
| AGENT-03 | P1 | Security/Agents | AGENT-02 | External effects ledger, effect idempotency & reconciliation; unknown outcome never replayed blindly. |
| FW-01 | P0 | Firmware | A0-01 | Button debouncing, I2S mono WAV capture, visible FSM, max duration; UART test captures valid WAV. |
| FW-02 | P0 | Firmware | FW-01,API-02 | Persistent recording queue and SHA-256; TLS upload with identical UUID on retry; 100 packet-loss simulations. |
| FW-03 | P0 | Firmware | FW-02 | LTE Cat 1 bis modem driver, APN provision, power management, TLS transport; actual SIM field test. |
| FW-04 | P1 | Hardware | FW-03 | 50+ upload cycles, signal variation and battery/power-profile report; brownout-free LTE TX. |
| UI-01 | P0 | Frontend | A0-01 | Next.js shell with auth and typed generated API client; route mock stories. |
| UI-02 | P0 | Frontend | UI-01,API-03,API-04 | Project list, recordings/transcripts, task/run status and event timelines. |
| UI-03 | P0 | Frontend | UI-01,POLICY-02 | Approval inbox, allow/deny, routing correction; tests for expiry and double-click. |
| OPS-01 | P0 | Platform | A0-02 | Compose stack, migrations, sealed secrets, reverse proxy HTTPS, backups and restore test. |
| OPS-02 | P1 | Platform | Q-01,AGENT-01 | Structured logs, metrics, traces, dashboards and alert thresholds. |
| QA-01 | P0 | QA | API-02,STT-01,ROUTE-02,AGENT-01,UI-02 | Browser voice demo end-to-end without device. |
| QA-02 | P0 | QA | FW-03,QA-01,POLICY-02 | End-to-end LTE demo with denial/review and duplicate replay cases. |
| SEC-01 | P1 | Security | AGENT-02,OPS-01 | Threat model and independent security review; container escape paths, replay, SSRF, auth, prompt injection. |

## Suggested independent agent assignments
1. **Architect/contracts agent:** A0-01..03, review only after freeze. Own `/contracts`, `/docs/adr`.
2. **Backend agent:** API-01..04, own `/services/api`, `/migrations` after DB review.
3. **AI router agent:** STT-01, ROUTE-01..02, own `/services/intelligence`, `/tests/router`.
4. **Security/policy agent:** POLICY-01..02, AGENT-03 advisory, own `/services/policy`.
5. **Executor agent:** AGENT-01..02, own `/services/executor`.
6. **Firmware agent:** FW-01..03, own `/firmware`.
7. **Frontend agent:** UI-01..03, own `/apps/dashboard`.
8. **DevOps/QA agent:** OPS-01..02, QA-01..02, own `/infra`, `/tests/e2e`.

## Sprint plan (dependency-driven, no fixed timing assumption)
- **Sprint A:** contracts, fixture datasets, DB migrations, Compose, API skeleton and mock firmware.
- **Sprint B:** upload, STT/router, project/task models, UI foundation, fake executor.
- **Sprint C:** real agent and policy broker, device LTE, dashboard approvals, integration E2E.
- **Sprint D:** fault injection, modem/battery profiling, field acceptance, security reviews.

## Definition of done for all tickets
Code reviewed; tests passing in CI; interfaces match OpenAPI/event contract; errors and audit logged; no secrets committed; README/runbook updated; demo evidence or test fixture attached; rollback path considered.

# VOX Agent Watch

watchOS client (`apps/watch/`) + FastAPI backend (`backend/`) for VOX Agent. Contract: `contracts/openapi.yaml` (v0.3.0, additive over the v0.2 spec in `docs/vox-agent-spec/`). Start with `docs/ARCHITECTURE.md`, `docs/IMPLEMENTATION_PLAN.md`, `docs/WHISTLE_FEASIBILITY.md`.

- Backend tests: `cd backend && python -m pytest -q`
- Watch core tests (macOS only): `cd apps/watch/VoxWatchCore && swift test`
- Never claim Whistle works on a watch without a physical-device run; never fake transcripts.
- Logs carry ids/status only — never transcript text or tokens.

## Agent skills

### Issue tracker

Issues live in GitHub Issues on `sanlega/watchvoice` (via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

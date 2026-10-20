"""VOX Agent API — transcript-first instructions (v0.3 additions).

v0.2 endpoints (recordings, projects, tasks, runs) are not implemented in this
repo yet; nothing here changes their contracts.
"""
import hashlib
import json
import logging
import os
import uuid
from datetime import timedelta

from fastapi import BackgroundTasks, Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from . import db
from .policy import HIGH_RISK
from .processor import EVENT_RECEIVED, audit, aware, process_instruction, sha256
from .runners import enqueue_dispatch, verify_callback_token
from .schemas import (Approval, ApprovalDecision, Instruction, InstructionReceipt, NewInstruction, RunEventIn,
                      RunProgress)

MAX_BODY_BYTES = 16 * 1024
ENDPOINT = "POST /v1/instructions"


class JsonFormatter(logging.Formatter):
    """Structured logs. Callers pass ids/status only: never transcript text or tokens."""
    def format(self, record):
        base = {"level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        return json.dumps(base | {k: v for k, v in record.__dict__.items()
                                  if k in {"instruction_id", "status", "intent", "trace_id", "code", "device_id"}})


log = logging.getLogger("vox.api")


class Problem(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        self.status, self.code, self.message, self.headers = status, code, message, headers


def problem_response(status, code, message, headers=None):
    return JSONResponse({"code": code, "message": message, "trace_id": str(uuid.uuid4())}, status, headers)


def create_app(database_url: str | None = None, rate_limit_per_minute: int = 20, process_inline: bool = True):
    app = FastAPI(title="VOX Agent API", version="0.3.0")
    vox_log = logging.getLogger("vox")
    if not vox_log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        vox_log.addHandler(handler)
        vox_log.setLevel(logging.INFO)
    app.state.engine = db.make_engine(database_url or os.environ.get("VOX_DATABASE_URL", "sqlite:///./vox-dev.db"))
    app.state.rate_limit = rate_limit_per_minute

    @app.exception_handler(Problem)
    async def _problem(_, exc: Problem):
        if exc.status >= 400:
            log.info("request_rejected", extra={"code": exc.code, "status": exc.status})
        return problem_response(exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_, exc):
        return problem_response(422, "validation_error", "Request failed validation")

    def device(authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise Problem(401, "unauthorized", "Missing bearer token", {"WWW-Authenticate": "Bearer"})
        with app.state.engine.connect() as conn:
            row = conn.execute(select(db.devices).where(
                db.devices.c.credential_hash == sha256(authorization[7:].strip()))).first()
        if row is None or row.revoked_at is not None:
            raise Problem(401, "unauthorized", "Invalid or revoked credential", {"WWW-Authenticate": "Bearer"})
        return row

    def require(dev, scope):
        if scope not in (dev.scopes or []):
            raise Problem(403, "forbidden", f"Device lacks scope {scope}")

    def receipt(ins) -> dict:
        return InstructionReceipt(instruction_id=ins.id, status="queued", created_at=aware(ins.created_at),
                                  links={"self": f"/v1/instructions/{ins.id}"}).model_dump(mode="json")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post("/v1/instructions", status_code=202)
    async def submit_instruction(request: Request, background: BackgroundTasks, dev=Depends(device),
                                 idempotency_key: str | None = Header(default=None)):
        require(dev, "instructions:write")
        if not idempotency_key or not 8 <= len(idempotency_key) <= 128:
            raise Problem(400, "idempotency_key_required", "Idempotency-Key header (8-128 chars) is required")
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            raise Problem(413, "payload_too_large", f"Body exceeds {MAX_BODY_BYTES} bytes")
        try:
            body = NewInstruction.model_validate_json(raw)
        except ValidationError:
            raise Problem(422, "validation_error", "Request failed validation")
        digest = hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
        engine = app.state.engine

        def replay_or_conflict(conn):
            idem = conn.execute(select(db.api_idempotency).where(
                db.api_idempotency.c.user_id == dev.user_id, db.api_idempotency.c.endpoint == ENDPOINT,
                db.api_idempotency.c.key == idempotency_key)).first()
            if idem is not None:
                if idem.request_digest != digest:
                    raise Problem(409, "idempotency_conflict", "Idempotency-Key reused with a different payload")
                return idem.response_json
            ins = conn.execute(select(db.instructions).where(
                db.instructions.c.user_id == dev.user_id,
                db.instructions.c.client_instruction_id == body.client_instruction_id)).first()
            if ins is not None:
                if ins.request_digest != digest:
                    raise Problem(409, "idempotency_conflict", "client_instruction_id reused with a different payload")
                return receipt(ins)
            return None

        with engine.connect() as conn:
            if (prior := replay_or_conflict(conn)) is not None:
                return JSONResponse(prior, 202, {"Idempotent-Replayed": "true"})
            since = db.now() - timedelta(seconds=60)
            recent = conn.execute(select(func.count()).select_from(db.instructions).where(
                db.instructions.c.user_id == dev.user_id, db.instructions.c.created_at >= since)).scalar_one()
        if recent >= app.state.rate_limit:
            raise Problem(429, "rate_limited", "Too many instructions", {"Retry-After": "60"})

        ins_id, trace_id, now = uuid.uuid4(), uuid.uuid4(), db.now()
        response = {"instruction_id": str(ins_id), "status": "queued", "created_at": now.isoformat(),
                    "links": {"self": f"/v1/instructions/{ins_id}"}}
        try:
            with engine.begin() as conn:
                conn.execute(db.instructions.insert().values(
                    id=ins_id, user_id=dev.user_id, device_id=dev.id, client_instruction_id=body.client_instruction_id,
                    source=body.source, text=body.text, language=body.language, project_hint=body.project_hint,
                    runner_hint=body.runner_hint,
                    transcription_metadata=body.transcription.model_dump(mode="json") | {"client": body.client.model_dump()},
                    request_digest=digest, status="queued", trace_id=trace_id, created_at=now, updated_at=now))
                conn.execute(db.instruction_events.insert().values(instruction_id=ins_id, event_type="instruction.received",
                                                                   safe_payload={"source": body.source,
                                                                                 "engine": body.transcription.engine}))
                conn.execute(db.outbox_events.insert().values(event_type=EVENT_RECEIVED, aggregate_id=ins_id,
                                                              trace_id=trace_id, payload={"instruction_id": str(ins_id)}))
                conn.execute(db.api_idempotency.insert().values(
                    user_id=dev.user_id, endpoint=ENDPOINT, key=idempotency_key, request_digest=digest,
                    status_code=202, response_json=response, expires_at=now + timedelta(days=7)))
                conn.execute(update(db.devices).where(db.devices.c.id == dev.id).values(last_seen_at=now))
        except IntegrityError:
            # Lost a race with an identical concurrent request: answer from what the winner stored.
            with engine.connect() as conn:
                if (prior := replay_or_conflict(conn)) is not None:
                    return JSONResponse(prior, 202, {"Idempotent-Replayed": "true"})
            raise
        log.info("instruction_received", extra={"instruction_id": str(ins_id), "trace_id": str(trace_id)})
        if process_inline:
            background.add_task(process_instruction, engine, ins_id)
        return JSONResponse(response, 202)

    @app.get("/v1/instructions/{instruction_id}", response_model=Instruction)
    def get_instruction(instruction_id: uuid.UUID, dev=Depends(device)):
        require(dev, "instructions:read")
        with app.state.engine.connect() as conn:
            ins = conn.execute(select(db.instructions).where(db.instructions.c.id == instruction_id,
                                                             db.instructions.c.user_id == dev.user_id)).first()
            if ins is None:
                raise Problem(404, "not_found", "Instruction not found")
            run = conn.execute(select(db.agent_runs, db.runners.c.name.label("runner_name"))
                               .outerjoin(db.runners, db.runners.c.id == db.agent_runs.c.runner_id)
                               .where(db.agent_runs.c.id == ins.agent_run_id)).first() if ins.agent_run_id else None
        progress = RunProgress(runner=run.runner_name, run_state=run.state, summary=run.summary,
                               pr_url=run.external_pr_url, updated_at=aware(run.last_event_at)) if run else None
        return Instruction(**{k: getattr(ins, k) for k in Instruction.model_fields
                              if k not in {"created_at", "updated_at", "progress"}},
                           progress=progress, created_at=aware(ins.created_at), updated_at=aware(ins.updated_at))

    def expire_stale(conn, user_id):
        conn.execute(update(db.approvals).where(db.approvals.c.user_id == user_id, db.approvals.c.state == "pending",
                                                db.approvals.c.expires_at < db.now()).values(state="expired"))

    @app.get("/v1/approvals", response_model=list[Approval])
    def list_approvals(status: str | None = None, dev=Depends(device)):
        require(dev, "approvals:read")
        with app.state.engine.begin() as conn:
            expire_stale(conn, dev.user_id)
            q = (select(db.approvals, db.tasks.c.project_id, db.approval_challenges.c.token)
                 .join(db.agent_runs, db.agent_runs.c.id == db.approvals.c.run_id)
                 .join(db.tasks, db.tasks.c.id == db.agent_runs.c.task_id)
                 .outerjoin(db.approval_challenges, (db.approval_challenges.c.approval_id == db.approvals.c.id)
                            & db.approval_challenges.c.consumed_at.is_(None))
                 .where(db.approvals.c.user_id == dev.user_id).order_by(db.approvals.c.created_at.desc()).limit(50))
            if status:
                q = q.where(db.approvals.c.state == status)
            rows = conn.execute(q).all()
        return [Approval(id=r.id, run_id=r.run_id, capability=r.capability, action_summary=r.action_summary,
                         state=r.state, expires_at=aware(r.expires_at), project_id=r.project_id,
                         risk="high" if r.capability in HIGH_RISK else "medium",
                         challenge_token=r.token if r.state == "pending" else None) for r in rows]

    @app.post("/v1/approvals/{approval_id}/decision", response_model=Approval)
    def decide(approval_id: uuid.UUID, body: ApprovalDecision, dev=Depends(device),
               idempotency_key: str | None = Header(default=None)):
        require(dev, "approvals:decide")
        if not idempotency_key or not 8 <= len(idempotency_key) <= 128:
            raise Problem(400, "idempotency_key_required", "Idempotency-Key header (8-128 chars) is required")
        now = db.now()
        with app.state.engine.begin() as conn:
            ap = conn.execute(select(db.approvals).where(db.approvals.c.id == approval_id,
                                                         db.approvals.c.user_id == dev.user_id)).first()
            if ap is None:
                raise Problem(404, "not_found", "Approval not found")
            if ap.state == body.decision:
                pass  # idempotent replay of the same decision
            elif ap.state != "pending":
                raise Problem(409, "approval_not_pending", f"Approval is {ap.state}")
            elif aware(ap.expires_at) <= now:
                conn.execute(update(db.approvals).where(db.approvals.c.id == ap.id).values(state="expired"))
                audit(conn, user_id=dev.user_id, run_id=ap.run_id, event_type="approval.expired",
                      subject_type="approval", subject_id=ap.id)
                return JSONResponse({"code": "approval_expired", "message": "Approval expired",
                                     "trace_id": str(uuid.uuid4())}, 409)  # commit the expiry, then reject
            else:
                ch = conn.execute(select(db.approval_challenges).where(
                    db.approval_challenges.c.approval_id == ap.id, db.approval_challenges.c.consumed_at.is_(None))).first()
                if (ch is None or not body.challenge_token or sha256(body.challenge_token) != ch.nonce_hash
                        or ch.action_fingerprint != ap.action_digest or aware(ch.expires_at) <= now):
                    raise Problem(403, "challenge_invalid", "Challenge token missing, invalid or not bound to this action")
                conn.execute(update(db.approval_challenges).where(db.approval_challenges.c.id == ch.id)
                             .values(consumed_at=now))
                conn.execute(update(db.approvals).where(db.approvals.c.id == ap.id).values(
                    state=body.decision, decided_by=dev.user_id, decided_at=now))
                approved = body.decision == "approved"
                run = conn.execute(select(db.agent_runs).where(db.agent_runs.c.id == ap.run_id)).first()
                conn.execute(update(db.agent_runs).where(db.agent_runs.c.id == run.id)
                             .values(state="queued" if approved else "cancelled"))
                if approved and run.runner_id:
                    trace = conn.execute(select(db.tasks.c.trace_id).where(db.tasks.c.id == run.task_id)).scalar_one()
                    enqueue_dispatch(conn, run.id, trace)
                conn.execute(update(db.tasks).where(db.tasks.c.id == run.task_id)
                             .values(state="queued" if approved else "cancelled", updated_at=now))
                conn.execute(update(db.instructions).where(db.instructions.c.approval_id == ap.id)
                             .values(status="dispatched" if approved else "cancelled", updated_at=now))
                audit(conn, user_id=dev.user_id, run_id=ap.run_id, event_type=f"approval.{body.decision}",
                      subject_type="approval", subject_id=ap.id, actor_type="device", actor_id=str(dev.id))
            ap = conn.execute(select(db.approvals).where(db.approvals.c.id == approval_id)).first()
        return Approval(id=ap.id, run_id=ap.run_id, capability=ap.capability, action_summary=ap.action_summary,
                        state=ap.state, expires_at=aware(ap.expires_at),
                        risk="high" if ap.capability in HIGH_RISK else "medium")

    TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}
    RUN_TO_TASK = {"running": "running", "succeeded": "completed", "failed": "failed"}

    @app.post("/v1/runs/{run_id}/events", status_code=204)
    def run_event(run_id: uuid.UUID, body: RunEventIn, authorization: str | None = Header(default=None)):
        token = authorization[7:].strip() if authorization and authorization.startswith("Bearer ") else ""
        if not verify_callback_token(run_id, token):
            raise Problem(401, "unauthorized", "Invalid run callback token", {"WWW-Authenticate": "Bearer"})
        now = db.now()
        with app.state.engine.begin() as conn:
            run = conn.execute(select(db.agent_runs).where(db.agent_runs.c.id == run_id)).first()
            if run is None:
                raise Problem(404, "not_found", "Run not found")
            if run.callback_expires_at is None or aware(run.callback_expires_at) <= now:
                raise Problem(401, "callback_expired", "Run callback window closed or run not dispatched")
            if run.state in TERMINAL:
                raise Problem(409, "run_terminal", f"Run is {run.state}")
            if run.state not in {"provisioning", "running"}:
                raise Problem(409, "run_not_dispatched", f"Run is {run.state}")
            state = {"succeeded": "succeeded", "failed": "failed"}.get(body.event, "running")
            values = {"state": state, "last_event_at": now}
            values |= {k: v for k, v in {"summary": body.summary, "external_pr_url": body.pr_url,
                                         "external_ref": body.external_ref}.items() if v is not None}
            conn.execute(update(db.agent_runs).where(db.agent_runs.c.id == run_id).values(**values))
            conn.execute(update(db.tasks).where(db.tasks.c.id == run.task_id)
                         .values(state=RUN_TO_TASK[state], updated_at=now))
            audit(conn, user_id=run.user_id, run_id=run_id, event_type=f"run.{body.event}", subject_type="run",
                  subject_id=run_id, actor_type="runner", actor_id=run.executor,
                  payload={"pr_url": body.pr_url, "external_ref": body.external_ref})  # summary not audited: agent text
        log.info("run_event", extra={"status": state, "code": body.event})
        return None

    return app


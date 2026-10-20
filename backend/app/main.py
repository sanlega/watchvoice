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

from . import control, db
from .deps import Problem, device, problem_response, require, require_idempotency_key
from .processor import EVENT_RECEIVED, aware, process_instruction
from .schemas import Instruction, InstructionReceipt, NewInstruction, RunProgress

MAX_BODY_BYTES = 16 * 1024
ENDPOINT = "POST /v1/instructions"


class JsonFormatter(logging.Formatter):
    """Structured logs. Callers pass ids/status only: never transcript text or tokens."""
    def format(self, record):
        base = {"level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        return json.dumps(base | {k: v for k, v in record.__dict__.items()
                                  if k in {"instruction_id", "status", "intent", "trace_id", "code", "device_id"}})


log = logging.getLogger("vox.api")


def create_app(database_url: str | None = None, rate_limit_per_minute: int = 20, process_inline: bool = True):
    app = FastAPI(title="VOX Agent API", version="0.4.0")
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

    def receipt(ins) -> dict:
        return InstructionReceipt(instruction_id=ins.id, status="queued", created_at=aware(ins.created_at),
                                  links={"self": f"/v1/instructions/{ins.id}"}).model_dump(mode="json")

    app.include_router(control.router)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post("/v1/instructions", status_code=202)
    async def submit_instruction(request: Request, background: BackgroundTasks, dev=Depends(device),
                                 idempotency_key: str | None = Header(default=None)):
        require(dev, "instructions:write")
        require_idempotency_key(idempotency_key)
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
        progress = RunProgress(runner=run.runner_name, agent=(run.agent or {}).get("key"), run_state=run.state,
                               summary=run.summary,
                               pr_url=run.external_pr_url, updated_at=aware(run.last_event_at)) if run else None
        return Instruction(**{k: getattr(ins, k) for k in Instruction.model_fields
                              if k not in {"created_at", "updated_at", "progress"}},
                           progress=progress, created_at=aware(ins.created_at), updated_at=aware(ins.updated_at))

    return app


"""Approvals, pairing, push registration, run callbacks, cancel, pause and policy (ADR-0001)."""
import re
import secrets
import uuid
from collections import defaultdict, deque
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, update

from . import db, devicesig, notify
from .deps import Problem, device, engine, require, require_idempotency_key, require_signature
from .policy import CATALOG, evaluate, risk_of
from .processor import apply_rule, audit, aware, create_approval, load_rules, sha256
from .runners import enqueue_cancel, enqueue_dispatch, verify_callback_token
from .schemas import (AgentApprovalIn, AgentApprovalOut, Approval, ApprovalDecision, PairIn, PairOut, PauseIn,
                      PolicyRuleOut, PushTokenIn, RunEventIn, SignedBody)

router = APIRouter()
PAIR_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L
PAIR_TTL = timedelta(minutes=10)
WATCH_SCOPES = ["instructions:write", "instructions:read", "approvals:read", "approvals:decide", "runs:control"]
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}
ACTIVE = {"queued", "provisioning", "running", "waiting_approval"}
RUN_TO_TASK = {"running": "running", "succeeded": "completed", "failed": "failed", "cancelled": "cancelled",
               "timed_out": "failed"}
MAX_PENDING_AGENT, MAX_AGENT_PER_RUN = 3, 20
_pair_attempts: dict[str, deque] = defaultdict(deque)


def new_pairing_code() -> str:
    return "".join(secrets.choice(PAIR_ALPHABET) for _ in range(8))


def sanitize(text: str, limit: int = 60) -> str:
    return re.sub(r"[\x00-\x1f\x7f​-‏‪-‮*_`#<>\[\]]", "", text).strip()[:limit]


# ---------------------------------------------------------------- pairing / push

@router.post("/v1/devices/pair", response_model=PairOut, status_code=201)
def pair(body: PairIn, request: Request):
    # shortcut: per-IP limiter in process memory (single API instance); move to Redis with multiple instances.
    ip, now = request.client.host if request.client else "?", db.now()
    attempts = _pair_attempts[ip]
    while attempts and now - attempts[0] > timedelta(minutes=10):
        attempts.popleft()
    if len(attempts) >= 5:
        raise Problem(429, "rate_limited", "Too many pairing attempts", {"Retry-After": "600"})
    attempts.append(now)
    try:
        devicesig.load_public_key(body.public_key)
    except ValueError:
        raise Problem(422, "public_key_invalid", "public_key must be base64 X9.63 P-256")
    with engine(request).begin() as conn:
        code = conn.execute(select(db.pairing_codes).where(
            db.pairing_codes.c.code_hash == sha256(body.code.upper()))).first()
        if code is None or code.used_at is not None or aware(code.expires_at) <= now:
            raise Problem(401, "pairing_code_invalid", "Pairing code invalid, used or expired")
        token, device_id = "vox_" + secrets.token_urlsafe(32), uuid.uuid4()
        conn.execute(update(db.pairing_codes).where(db.pairing_codes.c.id == code.id).values(used_at=now))
        if code.replace_device_id:
            conn.execute(update(db.devices).where(db.devices.c.id == code.replace_device_id).values(revoked_at=now))
        conn.execute(db.devices.insert().values(
            id=device_id, user_id=code.user_id, name=body.name, hardware_model="apple_watch",
            credential_hash=sha256(token), scopes=WATCH_SCOPES, public_key=body.public_key))
        audit(conn, user_id=code.user_id, event_type="device.paired", subject_type="device", subject_id=device_id,
              actor_type="device", actor_id=str(device_id),
              payload={"replaced": str(code.replace_device_id) if code.replace_device_id else None})
    attempts.clear()
    return PairOut(device_id=device_id, token=token)


@router.put("/v1/devices/me/push-token", status_code=204)
def push_token(body: PushTokenIn, request: Request, dev=Depends(device)):
    with engine(request).begin() as conn:
        conn.execute(update(db.devices).where(db.devices.c.id == dev.id)
                     .values(apns_token=body.token, apns_env=body.environment))


# ---------------------------------------------------------------- approvals

def _approval_out(conn, ap) -> Approval:
    project_id = runner = trust = None
    if ap.run_id:
        row = conn.execute(select(db.tasks.c.project_id, db.runners.c.name, db.runners.c.trust)
                           .select_from(db.agent_runs.join(db.tasks, db.tasks.c.id == db.agent_runs.c.task_id)
                                        .outerjoin(db.runners, db.runners.c.id == db.agent_runs.c.runner_id))
                           .where(db.agent_runs.c.id == ap.run_id)).first()
        project_id, runner, trust = row
    elif ap.kind == "policy_change":
        project_id = ap.payload.get("project_id")
        if rid := ap.payload.get("runner_id"):
            runner, trust = conn.execute(select(db.runners.c.name, db.runners.c.trust)
                                         .where(db.runners.c.id == uuid.UUID(rid))).first()
    return Approval(id=ap.id, kind=ap.kind, run_id=ap.run_id, capability=ap.capability,
                    action_summary=ap.action_summary, agent_provided=ap.kind == "agent_request", state=ap.state,
                    risk=ap.risk, expires_at=aware(ap.expires_at), action_fingerprint=ap.action_digest,
                    project_id=project_id, runner=runner, runner_trust=trust)


def _expire_stale(conn, user_id):
    conn.execute(update(db.approvals).where(db.approvals.c.user_id == user_id, db.approvals.c.state == "pending",
                                            db.approvals.c.expires_at < db.now()).values(state="expired"))


@router.get("/v1/approvals", response_model=list[Approval])
def list_approvals(request: Request, status: str | None = None, dev=Depends(device)):
    require(dev, "approvals:read")
    with engine(request).begin() as conn:
        _expire_stale(conn, dev.user_id)
        q = select(db.approvals).where(db.approvals.c.user_id == dev.user_id)
        if status:
            q = q.where(db.approvals.c.state == status)
        return [_approval_out(conn, ap) for ap in
                conn.execute(q.order_by(db.approvals.c.created_at.desc()).limit(50)).all()]


def _set_run_state(conn, run, state, now):
    conn.execute(update(db.agent_runs).where(db.agent_runs.c.id == run.id).values(state=state, last_event_at=now))
    conn.execute(update(db.tasks).where(db.tasks.c.id == run.task_id)
                 .values(state=RUN_TO_TASK.get(state, state), updated_at=now))


def _apply_decision(conn, ap, approved: bool, dev, now):
    if ap.kind == "action":
        run = conn.execute(select(db.agent_runs).where(db.agent_runs.c.id == ap.run_id)).one()
        _set_run_state(conn, run, "queued" if approved else "cancelled", now)
        if approved and run.runner_id:
            trace = conn.execute(select(db.tasks.c.trace_id).where(db.tasks.c.id == run.task_id)).scalar_one()
            enqueue_dispatch(conn, run.id, trace)
        status = "dispatched" if approved else "cancelled"
    elif ap.kind == "policy_change":
        if approved:
            p = ap.payload
            apply_rule(conn, ap.user_id, runner_id=uuid.UUID(p["runner_id"]) if p["runner_id"] else None,
                       project_id=uuid.UUID(p["project_id"]) if p["project_id"] else None, mode=p["mode"],
                       effects=p["effects"], source="voice",
                       expires_at=now + timedelta(seconds=p["duration_s"]) if p["duration_s"] else None)
        status = "applied" if approved else "cancelled"
    else:
        status = None  # agent_request: the agent polls its request
    if status and ap.instruction_id:
        conn.execute(update(db.instructions).where(db.instructions.c.id == ap.instruction_id)
                     .values(status=status, updated_at=now))


@router.post("/v1/approvals/{approval_id}/decision", response_model=Approval)
def decide(approval_id: uuid.UUID, body: ApprovalDecision, request: Request, dev=Depends(device),
           idempotency_key: str | None = Header(default=None)):
    require(dev, "approvals:decide")
    require_idempotency_key(idempotency_key)
    require_signature(dev, {"approval_id": str(approval_id), "action_fingerprint": body.action_fingerprint,
                            "decision": body.decision, "device_id": str(dev.id), "ts": body.ts}, body.signature)
    now = db.now()
    with engine(request).begin() as conn:
        ap = conn.execute(select(db.approvals).where(db.approvals.c.id == approval_id,
                                                     db.approvals.c.user_id == dev.user_id)).first()
        if ap is None:
            raise Problem(404, "not_found", "Approval not found")
        if ap.action_digest != body.action_fingerprint:
            raise Problem(409, "fingerprint_mismatch", "The action changed; review it again")
        if ap.state == body.decision:
            return _approval_out(conn, ap)  # idempotent replay
        if ap.state != "pending":
            raise Problem(409, "approval_not_pending", f"Approval is {ap.state}")
        if aware(ap.expires_at) <= now:
            conn.execute(update(db.approvals).where(db.approvals.c.id == ap.id).values(state="expired"))
            audit(conn, user_id=dev.user_id, run_id=ap.run_id, event_type="approval.expired",
                  subject_type="approval", subject_id=ap.id)
            return JSONResponse({"code": "approval_expired", "message": "Approval expired",
                                 "trace_id": str(uuid.uuid4())}, 409)  # commit the expiry, then reject
        conn.execute(update(db.approvals).where(db.approvals.c.id == ap.id).values(
            state=body.decision, decided_by=dev.user_id, decided_at=now))
        _apply_decision(conn, ap, body.decision == "approved", dev, now)
        audit(conn, user_id=dev.user_id, run_id=ap.run_id, event_type=f"approval.{body.decision}",
              subject_type="approval", subject_id=ap.id, actor_type="device", actor_id=str(dev.id),
              payload={"kind": ap.kind})
        return _approval_out(conn, conn.execute(select(db.approvals).where(db.approvals.c.id == ap.id)).one())


# ---------------------------------------------------------------- run callbacks (T3 agents)

def _run_for_callback(conn, run_id, authorization, now):
    token = authorization[7:].strip() if authorization and authorization.startswith("Bearer ") else ""
    if not verify_callback_token(run_id, token):
        raise Problem(401, "unauthorized", "Invalid run callback token", {"WWW-Authenticate": "Bearer"})
    run = conn.execute(select(db.agent_runs).where(db.agent_runs.c.id == run_id)).first()
    if run is None:
        raise Problem(404, "not_found", "Run not found")
    if run.callback_expires_at is None or aware(run.callback_expires_at) <= now:
        raise Problem(401, "callback_expired", "Run callback window closed or run not dispatched")
    if run.state in TERMINAL:
        raise Problem(409, "run_terminal", f"Run is {run.state}")
    if run.state not in {"provisioning", "running"}:
        raise Problem(409, "run_not_dispatched", f"Run is {run.state}")
    return run


EVENT_PUSH = {"needs_input": ("time-sensitive", "VOX: necesita respuesta"), "failed": ("active", "VOX: falló"),
              "succeeded": ("passive", "VOX: completado")}


@router.post("/v1/runs/{run_id}/events", status_code=204)
def run_event(run_id: uuid.UUID, body: RunEventIn, request: Request, authorization: str | None = Header(default=None)):
    now = db.now()
    with engine(request).begin() as conn:
        run = _run_for_callback(conn, run_id, authorization, now)
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
        if body.event in EVENT_PUSH:
            level, title = EVENT_PUSH[body.event]
            text = "PR lista" if body.pr_url else sanitize(body.summary or "", 100) or body.event
            notify.enqueue(conn, run.user_id, f"run.{body.event}", level, title, text, run_id=run_id)
    return None


@router.post("/v1/runs/{run_id}/approval-requests", response_model=AgentApprovalOut, status_code=201)
def agent_approval_request(run_id: uuid.UUID, body: AgentApprovalIn, request: Request,
                           authorization: str | None = Header(default=None)):
    now = db.now()
    with engine(request).begin() as conn:
        run = _run_for_callback(conn, run_id, authorization, now)
        capability = body.capability  # unknown capabilities: ask (or allow under bypass), shown as high risk
        project_id = conn.execute(select(db.tasks.c.project_id).where(db.tasks.c.id == run.task_id)).scalar_one()
        decision = evaluate({capability}, load_rules(conn, run.user_id), run.runner_id, project_id, now)
        effect = decision.effect
        audit(conn, user_id=run.user_id, run_id=run_id, event_type="agent.approval_requested", subject_type="run",
              subject_id=run_id, actor_type="runner", actor_id=run.executor,
              payload={"capability": capability, "effect": effect})
        if effect != "ask":
            return AgentApprovalOut(approval_id=None, state="approved" if effect == "allow" else "denied",
                                    expires_at=None)
        mine = select(func.count()).select_from(db.approvals).where(db.approvals.c.run_id == run_id,
                                                                   db.approvals.c.kind == "agent_request")
        if conn.execute(mine.where(db.approvals.c.state == "pending")).scalar_one() >= MAX_PENDING_AGENT \
                or conn.execute(mine).scalar_one() >= MAX_AGENT_PER_RUN:
            raise Problem(429, "approval_quota", "Too many approval requests for this run")
        risk = risk_of(capability) if capability in CATALOG else "high"
        approval_id = create_approval(conn, user_id=run.user_id, kind="agent_request", capability=capability,
                                      summary=sanitize(body.action_summary), risk=risk, now=now, run_id=run_id,
                                      payload={"details_sha256": sha256(str(sorted(body.details.items())))})
        ap = conn.execute(select(db.approvals).where(db.approvals.c.id == approval_id)).one()
    return AgentApprovalOut(approval_id=approval_id, state="pending", expires_at=aware(ap.expires_at))


@router.get("/v1/runs/{run_id}/approval-requests/{approval_id}", response_model=AgentApprovalOut)
def agent_approval_status(run_id: uuid.UUID, approval_id: uuid.UUID, request: Request,
                          authorization: str | None = Header(default=None)):
    now = db.now()
    with engine(request).begin() as conn:
        _run_for_callback(conn, run_id, authorization, now)
        ap = conn.execute(select(db.approvals).where(db.approvals.c.id == approval_id,
                                                     db.approvals.c.run_id == run_id)).first()
        if ap is None:
            raise Problem(404, "not_found", "Approval request not found")
        state = "expired" if ap.state == "pending" and aware(ap.expires_at) <= now else ap.state
    return AgentApprovalOut(approval_id=ap.id, state=state, expires_at=aware(ap.expires_at))


# ---------------------------------------------------------------- cancel / pause / policy

def cancel_run(conn, run, now, reason="cancelled"):
    dispatched = run.state in {"provisioning", "running"}
    _set_run_state(conn, run, reason, now)
    conn.execute(update(db.approvals).where(db.approvals.c.run_id == run.id, db.approvals.c.state == "pending")
                 .values(state="cancelled"))
    if dispatched and run.runner_id:
        enqueue_cancel(conn, run, run.id)


@router.post("/v1/runs/{run_id}/cancel", status_code=202)
def cancel(run_id: uuid.UUID, body: SignedBody, request: Request,
           dev=Depends(device)):
    require(dev, "runs:control")
    require_signature(dev, {"action": "cancel_run", "run_id": str(run_id), "device_id": str(dev.id),
                            "ts": body.ts}, body.signature)
    now = db.now()
    with engine(request).begin() as conn:
        run = conn.execute(select(db.agent_runs).where(db.agent_runs.c.id == run_id,
                                                       db.agent_runs.c.user_id == dev.user_id)).first()
        if run is None:
            raise Problem(404, "not_found", "Run not found")
        if run.state in TERMINAL:
            return {"state": run.state}
        cancel_run(conn, run, now)
        audit(conn, user_id=dev.user_id, run_id=run_id, event_type="run.cancel_requested", subject_type="run",
              subject_id=run_id, actor_type="device", actor_id=str(dev.id))
    return {"state": "cancelled"}


@router.post("/v1/pause")
def pause(body: PauseIn, request: Request, dev=Depends(device)):
    require(dev, "runs:control")
    require_signature(dev, {"action": "pause" if body.paused else "resume", "device_id": str(dev.id),
                            "ts": body.ts}, body.signature)
    now, cancelled = db.now(), 0
    with engine(request).begin() as conn:
        conn.execute(db.user_controls.delete().where(db.user_controls.c.user_id == dev.user_id))
        conn.execute(db.user_controls.insert().values(user_id=dev.user_id, paused_at=now if body.paused else None))
        if body.paused:
            for run in conn.execute(select(db.agent_runs).where(db.agent_runs.c.user_id == dev.user_id,
                                                                db.agent_runs.c.state.in_(ACTIVE))).all():
                cancel_run(conn, run, now)
                cancelled += 1
        audit(conn, user_id=dev.user_id, event_type="control.pause" if body.paused else "control.resume",
              subject_type="user", subject_id=dev.user_id, actor_type="device", actor_id=str(dev.id),
              payload={"cancelled_runs": cancelled})
    return {"paused": body.paused, "cancelled_runs": cancelled}


@router.get("/v1/policy", response_model=list[PolicyRuleOut])
def effective_policy(request: Request, dev=Depends(device)):
    require(dev, "approvals:read")
    now = db.now()
    with engine(request).connect() as conn:
        names = dict(conn.execute(select(db.runners.c.id, db.runners.c.name)).all())
        projects = dict(conn.execute(select(db.projects.c.id, db.projects.c.name)).all())
        rules = [r for r in load_rules(conn, dev.user_id)
                 if r["revoked_at"] is None and (r["expires_at"] is None or r["expires_at"] > now)]
    return [PolicyRuleOut(id=r["id"], runner=names.get(r["runner_id"]), project=projects.get(r["project_id"]),
                          mode=r["mode"], effects=r["effects"], source=r["source"], expires_at=r["expires_at"])
            for r in rules]

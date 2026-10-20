"""Instruction -> route -> policy -> task/run/approval (A5 router adapter).

Idempotent: only instructions in `queued` are processed, and tasks.instruction_id
is UNIQUE, so replays of the outbox event can never duplicate a task.
"""
import hashlib
import json
import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from . import db
from .policy import HIGH_RISK, evaluate, requested_capabilities
from .router import HybridRouter, ProjectRef, norm
from .runners import enqueue_dispatch, select_runner

log = logging.getLogger("vox.processor")
APPROVAL_TTL = timedelta(minutes=15)
EVENT_RECEIVED = "instruction.received.v1"


def aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; everything we store is UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def audit(conn, *, user_id, event_type, subject_type, subject_id, payload=None, run_id=None, trace_id=None,
          actor_type="system", actor_id="instruction-processor"):
    conn.execute(db.audit_events.insert().values(
        user_id=user_id, run_id=run_id, actor_type=actor_type, actor_id=actor_id, event_type=event_type,
        subject_type=subject_type, subject_id=str(subject_id), payload=payload or {}, trace_id=trace_id))


def visible_projects(conn, user_id) -> list[ProjectRef]:
    rows = conn.execute(select(db.projects).where(db.projects.c.user_id == user_id,
                                                  db.projects.c.archived_at.is_(None))).all()
    aliases: dict = {}
    for a in conn.execute(select(db.project_aliases.c.project_id, db.project_aliases.c.alias)
                          .where(db.project_aliases.c.project_id.in_([r.id for r in rows]))):
        aliases.setdefault(a.project_id, []).append(a.alias)
    return [ProjectRef(r.id, r.name, aliases.get(r.id, []), r.description) for r in rows]


def project_context(conn, project_id) -> dict | None:
    if project_id is None:
        return None
    return conn.execute(select(db.projects.c.context_json).where(db.projects.c.id == project_id)).scalar()


def _slug(conn, user_id, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", norm(name)).strip("-")[:50] or "proyecto"
    base = base if len(base) >= 2 else f"p-{base}"
    taken = set(conn.execute(select(db.projects.c.slug).where(db.projects.c.user_id == user_id,
                                                              db.projects.c.slug.like(f"{base}%"))).scalars())
    return next(s for s in [base, *(f"{base}-{i}" for i in range(2, 1000))] if s not in taken)


def process_instruction(engine, instruction_id, router: HybridRouter | None = None) -> str | None:
    router = router or HybridRouter()
    with engine.begin() as conn:
        q = select(db.instructions).where(db.instructions.c.id == instruction_id)
        if conn.dialect.name == "postgresql":
            q = q.with_for_update()
        ins = conn.execute(q).first()
        if ins is None or ins.status != "queued":
            return ins.status if ins else None
        now = db.now()
        projects = visible_projects(conn, ins.user_id)
        route = router.route(ins.text, projects, ins.project_hint)
        values = {"intent": route.intent, "updated_at": now, "version": ins.version + 1}

        conn.execute(db.routing_decisions.insert().values(
            instruction_id=ins.id, user_id=ins.user_id, project_id=route.project_id, intent=route.intent,
            proposed_project_name=route.new_project_name, task_title=route.task_title,
            agent_type="coding", confidence=route.confidence,
            candidates=[{"project_id": str(c.id), "name": c.name} for c in route.candidates],
            reasoning_summary=route.reason, model_version=route.model_version, needs_review=route.needs_review))

        if route.needs_review:
            values |= {"status": "needs_clarification", "error_code": "ambiguous_route",
                       "candidates": [{"project_id": str(c.id), "name": c.name} for c in route.candidates]}
        elif route.intent == "capture_idea":
            values |= {"status": "captured", "project_id": route.project_id}
        elif (runner_sel := select_runner(conn, ins.user_id, ins.text, ins.runner_hint, project_context(
                conn, route.project_id)))[1]:
            values |= {"status": "needs_clarification", "error_code": runner_sel[1]}
        else:
            runner, project_id = runner_sel[0], route.project_id
            if route.intent == "new_project":
                project_id = uuid.uuid4()
                conn.execute(db.projects.insert().values(
                    id=project_id, user_id=ins.user_id, slug=_slug(conn, ins.user_id, route.new_project_name),
                    name=route.new_project_name, description=""))
                audit(conn, user_id=ins.user_id, event_type="project.created", subject_type="project",
                      subject_id=project_id, trace_id=ins.trace_id, payload={"instruction_id": str(ins.id)})

            grants = [g._asdict() | {"expires_at": aware(g.expires_at)} for g in conn.execute(
                select(db.capability_grants).where(db.capability_grants.c.user_id == ins.user_id))]
            decision = evaluate(requested_capabilities(ins.text), grants, project_id, now)
            gated = decision.effect == "approval_required"
            task_id, run_id = uuid.uuid4(), uuid.uuid4()
            conn.execute(db.tasks.insert().values(
                id=task_id, user_id=ins.user_id, project_id=project_id, instruction_id=ins.id,
                title=route.task_title or "Instrucción", description=ins.text, intent=route.intent,
                agent_type="coding", state="waiting_approval" if gated else "queued", trace_id=ins.trace_id))
            conn.execute(db.agent_runs.insert().values(
                id=run_id, user_id=ins.user_id, task_id=task_id, attempt=1,
                executor=f"t3code:{runner.name}" if runner else "unassigned", runner_id=runner.id if runner else None,
                summary=None if runner else "Sin máquina asignada", state="waiting_approval" if gated else "queued"))
            if runner and not gated:
                enqueue_dispatch(conn, run_id, ins.trace_id)
            values |= {"status": "awaiting_approval" if gated else "dispatched", "project_id": project_id,
                       "task_id": task_id, "agent_run_id": run_id}
            if gated:
                values["approval_id"] = create_approval(conn, ins, run_id, project_id, decision.capability, now)
            conn.execute(db.outbox_events.insert().values(
                event_type="task.created.v1", aggregate_id=task_id, trace_id=ins.trace_id,
                payload={"task_id": str(task_id), "run_id": str(run_id), "gated": gated}))
            audit(conn, user_id=ins.user_id, run_id=run_id, event_type="task.created", subject_type="task",
                  subject_id=task_id, trace_id=ins.trace_id,
                  payload={"policy": decision.effect, "capability": decision.capability})

        conn.execute(update(db.instructions).where(db.instructions.c.id == ins.id).values(**values))
        conn.execute(db.instruction_events.insert().values(
            instruction_id=ins.id, event_type=f"instruction.{values['status']}",
            safe_payload={"intent": route.intent, "reason": route.reason}))
        conn.execute(update(db.outbox_events).where(db.outbox_events.c.aggregate_id == ins.id,
                                                    db.outbox_events.c.event_type == EVENT_RECEIVED,
                                                    db.outbox_events.c.published_at.is_(None))
                     .values(published_at=now))
    log.info("instruction_processed", extra={"instruction_id": str(instruction_id), "status": values["status"],
                                              "intent": route.intent})
    return values["status"]


def create_approval(conn, ins, run_id, project_id, capability, now) -> uuid.UUID:
    approval_id = uuid.uuid4()
    name = conn.execute(select(db.projects.c.name).where(db.projects.c.id == project_id)).scalar_one()
    summary = f"{capability} en {name}"
    digest = sha256(json.dumps({"run_id": str(run_id), "capability": capability, "project_id": str(project_id),
                                "instruction_digest": ins.request_digest}, sort_keys=True))
    conn.execute(db.approvals.insert().values(
        id=approval_id, user_id=ins.user_id, run_id=run_id, capability=capability, action_summary=summary,
        action_digest=digest, state="pending", expires_at=now + APPROVAL_TTL))
    token = secrets.token_urlsafe(32)
    conn.execute(db.approval_challenges.insert().values(
        approval_id=approval_id, action_fingerprint=digest, nonce_hash=sha256(token), token=token,
        expires_at=now + APPROVAL_TTL, principal_id=ins.user_id))
    audit(conn, user_id=ins.user_id, run_id=run_id, event_type="approval.requested", subject_type="approval",
          subject_id=approval_id, trace_id=ins.trace_id,
          payload={"capability": capability, "risk": "high" if capability in HIGH_RISK else "medium"})
    return approval_id


def drain_outbox(engine, router: HybridRouter | None = None, limit: int = 100) -> int:
    """Crash recovery / worker loop: process every unpublished instruction.received event."""
    with engine.connect() as conn:
        ids = conn.execute(select(db.outbox_events.c.aggregate_id)
                           .where(db.outbox_events.c.event_type == EVENT_RECEIVED,
                                  db.outbox_events.c.published_at.is_(None))
                           .order_by(db.outbox_events.c.created_at).limit(limit)).scalars().all()
    for i in ids:
        process_instruction(engine, i, router)
    return len(ids)

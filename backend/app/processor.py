"""Instruction -> route -> policy -> task/run/approval (A5 router adapter).

Idempotent: only instructions in `queued` are processed, and tasks.instruction_id
is UNIQUE, so replays of the outbox event can never duplicate a task.
"""
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update

from . import db, notify, policy_voice
from .notify import APPROVAL_TTL
from .policy import evaluate, requested_capabilities
from .router import HybridRouter, ProjectRef, norm
from .router_jev import agent_for, jev_from_env, runner_refs
from .runners import enqueue_dispatch, enqueue_message, select_runner

log = logging.getLogger("vox.processor")
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


def load_rules(conn, user_id) -> list[dict]:
    return [r._asdict() | {"expires_at": aware(r.expires_at), "revoked_at": aware(r.revoked_at)}
            for r in conn.execute(select(db.policy_rules).where(db.policy_rules.c.user_id == user_id))]


def apply_rule(conn, user_id, *, runner_id, project_id, mode, effects, expires_at, source, trace_id=None) -> uuid.UUID:
    rule_id = uuid.uuid4()
    conn.execute(db.policy_rules.insert().values(
        id=rule_id, user_id=user_id, runner_id=runner_id, project_id=project_id, mode=mode, effects=effects,
        source=source, expires_at=expires_at))
    audit(conn, user_id=user_id, event_type="policy.rule_applied", subject_type="policy_rule", subject_id=rule_id,
          trace_id=trace_id, payload={"mode": mode, "runner_id": str(runner_id) if runner_id else None,
                                      "project_id": str(project_id) if project_id else None,
                                      "expires_at": expires_at.isoformat() if expires_at else None, "source": source})
    return rule_id


def create_approval(conn, *, user_id, kind, capability, summary, risk, now, run_id=None, instruction_id=None,
                    payload=None, trace_id=None) -> uuid.UUID:
    approval_id = uuid.uuid4()
    fingerprint = sha256(json.dumps({"approval_id": str(approval_id), "kind": kind, "capability": capability,
                                     "run_id": str(run_id) if run_id else None, "payload": payload or {}},
                                    sort_keys=True, default=str))
    expires_at = now + APPROVAL_TTL[risk]
    conn.execute(db.approvals.insert().values(
        id=approval_id, user_id=user_id, run_id=run_id, instruction_id=instruction_id, kind=kind, risk=risk,
        payload=payload or {}, capability=capability, action_summary=summary[:200], action_digest=fingerprint,
        state="pending", expires_at=expires_at))
    audit(conn, user_id=user_id, run_id=run_id, event_type="approval.requested", subject_type="approval",
          subject_id=approval_id, trace_id=trace_id, payload={"kind": kind, "capability": capability, "risk": risk})
    notify.enqueue(conn, user_id, "approval", "time-sensitive", "VOX: aprobación", summary,
                   category="VOX_APPROVAL_HIGH" if risk == "high" else "VOX_APPROVAL_LOW",
                   approval_id=approval_id, run_id=run_id, fingerprint=fingerprint, expires_at=expires_at)
    return approval_id


def handle_policy_change(conn, ins, change, now) -> dict:
    scope = {"runner_id": change.runner_id, "project_id": change.project_id}
    if change.direction == "tighten":  # only reduces risk: applies at once (ADR-0001 §4)
        apply_rule(conn, ins.user_id, **scope, mode="granular", effects=change.effects,
                   expires_at=now + change.duration if change.duration else None, source="voice", trace_id=ins.trace_id)
        return {"status": "applied"}
    payload = {k: str(v) if v else None for k, v in scope.items()} | {
        "mode": "bypass", "effects": {}, "duration_s": int(change.duration.total_seconds()) if change.duration else None}
    approval_id = create_approval(conn, user_id=ins.user_id, kind="policy_change", capability="policy.loosen",
                                  summary=change.summary, risk=change.risk, now=now, instruction_id=ins.id,
                                  payload=payload, trace_id=ins.trace_id)
    return {"status": "awaiting_approval", "approval_id": approval_id}


def process_instruction(engine, instruction_id, router: HybridRouter | None = None, jev=None) -> str | None:
    router = router or HybridRouter()
    jev = jev if jev is not None else jev_from_env()
    with engine.begin() as conn:
        q = select(db.instructions).where(db.instructions.c.id == instruction_id)
        if conn.dialect.name == "postgresql":
            q = q.with_for_update()
        ins = conn.execute(q).first()
        if ins is None or ins.status != "queued":
            return ins.status if ins else None
        now = db.now()
        projects = visible_projects(conn, ins.user_id)
        runner_list = conn.execute(select(db.runners).where(db.runners.c.user_id == ins.user_id,
                                                            db.runners.c.enabled.is_(True))).all()
        values = {"updated_at": now, "version": ins.version + 1}
        change = policy_voice.parse(ins.text, runner_list, projects)
        if change is not None:
            values["intent"], reason = "policy_change", "policy_voice"
            if isinstance(change, policy_voice.Clarify):
                values |= {"status": "needs_clarification", "error_code": change.error_code}
            else:
                values |= handle_policy_change(conn, ins, change, now)
        elif ins.reply_to_run_id is not None:
            values["intent"], reason = "continue_task", "reply"
            values |= follow_up(conn, ins, ins.reply_to_run_id, now)
        else:
            decision = None
            if jev and ins.project_hint is None:
                try:
                    decision = jev.decide(ins.text, projects, runner_refs(runner_list))
                except Exception as exc:  # model/API down: the deterministic rules are the safe fallback
                    log.warning("jev_failed", extra={"instruction_id": str(ins.id), "code": type(exc).__name__})
            route = decision.route if decision else router.route(ins.text, projects, ins.project_hint)
            values |= route_and_dispatch(conn, ins, route, now, decision)
            reason = values.pop("_reason")

        conn.execute(update(db.instructions).where(db.instructions.c.id == ins.id).values(**values))
        conn.execute(db.instruction_events.insert().values(
            instruction_id=ins.id, event_type=f"instruction.{values['status']}",
            safe_payload={"intent": values.get("intent"), "reason": reason}))
        conn.execute(update(db.outbox_events).where(db.outbox_events.c.aggregate_id == ins.id,
                                                    db.outbox_events.c.event_type == EVENT_RECEIVED,
                                                    db.outbox_events.c.published_at.is_(None))
                     .values(published_at=now))
    log.info("instruction_processed", extra={"instruction_id": str(instruction_id), "status": values["status"],
                                              "intent": values.get("intent")})
    return values["status"]


def route_and_dispatch(conn, ins, route, now, jev_decision=None) -> dict:
    values = {"intent": route.intent, "_reason": route.reason}
    conn.execute(db.routing_decisions.insert().values(
        instruction_id=ins.id, user_id=ins.user_id, project_id=route.project_id, intent=route.intent,
        proposed_project_name=route.new_project_name, task_title=route.task_title,
        agent_type="coding", confidence=route.confidence,
        candidates=[{"project_id": str(c.id), "name": c.name} for c in route.candidates],
        reasoning_summary=route.reason, model_version=route.model_version, needs_review=route.needs_review))
    if route.needs_review:
        return values | {"status": "needs_clarification", "error_code": "ambiguous_route",
                         "candidates": [{"project_id": str(c.id), "name": c.name} for c in route.candidates]}
    if route.intent == "capture_idea":
        notes = notes_project(conn, ins.user_id)
        if notes is None:  # no notes project registered: the idea stays stored in VOX only
            return values | {"status": "captured", "project_id": route.project_id}
        route.project_id = notes
    if route.intent == "continue_task" and route.project_id is not None:
        if (prev := latest_conversation(conn, ins.user_id, route.project_id)) is not None:
            return values | follow_up(conn, ins, prev.id, now)
    runner_error = jev_decision.runner_error if jev_decision and not ins.runner_hint else None
    runner = None
    if not runner_error:
        runner, runner_error = select_runner(conn, ins.user_id, ins.text, ins.runner_hint,
                                             project_context(conn, route.project_id),
                                             named=jev_decision.runner_name if jev_decision else None,
                                             project_id=route.project_id)
    agent, agent_error = (agent_for(runner, jev_decision.agent_key if jev_decision else None) if runner
                          else (None, None))
    if runner_error or agent_error:
        return values | {"status": "needs_clarification", "error_code": runner_error or agent_error}

    project_id = route.project_id
    decision = evaluate(requested_capabilities(ins.text), load_rules(conn, ins.user_id),
                        runner.id if runner else None, project_id, now)
    if decision.effect == "deny":
        audit(conn, user_id=ins.user_id, event_type="policy.denied", subject_type="instruction", subject_id=ins.id,
              trace_id=ins.trace_id, payload={"capability": decision.capability})
        return values | {"status": "failed", "error_code": "policy_denied"}
    if route.intent == "new_project":
        project_id = uuid.uuid4()
        conn.execute(db.projects.insert().values(
            id=project_id, user_id=ins.user_id, slug=_slug(conn, ins.user_id, route.new_project_name),
            name=route.new_project_name, description="", context_json={"create_on_runner": True}))
        audit(conn, user_id=ins.user_id, event_type="project.created", subject_type="project",
              subject_id=project_id, trace_id=ins.trace_id, payload={"instruction_id": str(ins.id)})

    gated = decision.effect == "ask"
    task_id, run_id = uuid.uuid4(), uuid.uuid4()
    conn.execute(db.tasks.insert().values(
        id=task_id, user_id=ins.user_id, project_id=project_id, instruction_id=ins.id,
        title=route.task_title or "Instrucción", description=ins.text, intent=route.intent,
        agent_type="coding", state="waiting_approval" if gated else "queued", trace_id=ins.trace_id))
    conn.execute(db.agent_runs.insert().values(
        id=run_id, user_id=ins.user_id, task_id=task_id, attempt=1,
        executor=f"t3code:{runner.name}" if runner else "unassigned", runner_id=runner.id if runner else None,
        runtime_mode=decision.runtime_mode, agent=agent, summary=None if runner else "Sin máquina asignada",
        state="waiting_approval" if gated else "queued"))
    if runner and not gated:
        enqueue_dispatch(conn, run_id, ins.trace_id)
    values |= {"status": "awaiting_approval" if gated else "dispatched", "project_id": project_id,
               "task_id": task_id, "agent_run_id": run_id}
    if gated:
        name = conn.execute(select(db.projects.c.name).where(db.projects.c.id == project_id)).scalar_one()
        where = f" · {runner.name}" if runner else ""
        values["approval_id"] = create_approval(
            conn, user_id=ins.user_id, kind="action", capability=decision.capability, risk=decision.risk,
            summary=f"{decision.capability} · {name}{where}", now=now, run_id=run_id, instruction_id=ins.id,
            trace_id=ins.trace_id)
    audit(conn, user_id=ins.user_id, run_id=run_id, event_type="task.created", subject_type="task",
          subject_id=task_id, trace_id=ins.trace_id,
          payload={"policy": decision.effect, "capability": decision.capability, "mode": decision.mode})
    return values


def notes_project(conn, user_id):
    for pid, ctx in conn.execute(select(db.projects.c.id, db.projects.c.context_json).where(
            db.projects.c.user_id == user_id, db.projects.c.archived_at.is_(None))):
        if (ctx or {}).get("notes"):
            return pid
    return None


def latest_conversation(conn, user_id, project_id):
    """Most recent run in this project whose T3 thread exists: 'sigue con eso' continues it."""
    return conn.execute(select(db.agent_runs).join(db.tasks, db.tasks.c.id == db.agent_runs.c.task_id).where(
        db.agent_runs.c.user_id == user_id, db.tasks.c.project_id == project_id,
        db.agent_runs.c.external_ref.is_not(None)).order_by(db.agent_runs.c.created_at.desc()).limit(1)).first()


def follow_up(conn, ins, prev_run_id, now) -> dict:
    """Send the instruction into the SAME T3 conversation as a new run attempt (run.message.v1).
    Policy is evaluated again on the new text; the agent and runtime mode of the thread are kept."""
    prev = conn.execute(select(db.agent_runs).where(db.agent_runs.c.id == prev_run_id,
                                                    db.agent_runs.c.user_id == ins.user_id)).first()
    if prev is None or prev.external_ref is None or prev.runner_id is None:
        return {"status": "needs_clarification", "error_code": "conversation_not_found"}
    project_id = conn.execute(select(db.tasks.c.project_id).where(db.tasks.c.id == prev.task_id)).scalar_one()
    decision = evaluate(requested_capabilities(ins.text), load_rules(conn, ins.user_id), prev.runner_id,
                        project_id, now)
    if decision.effect == "deny":
        return {"status": "failed", "error_code": "policy_denied", "project_id": project_id}
    attempt = conn.execute(select(db.agent_runs.c.attempt).where(db.agent_runs.c.task_id == prev.task_id)
                           .order_by(db.agent_runs.c.attempt.desc()).limit(1)).scalar_one() + 1
    gated, run_id = decision.effect == "ask", uuid.uuid4()
    conn.execute(db.agent_runs.insert().values(
        id=run_id, user_id=ins.user_id, task_id=prev.task_id, attempt=attempt, executor=prev.executor,
        runner_id=prev.runner_id, runtime_mode=prev.runtime_mode, agent=prev.agent, external_ref=prev.external_ref,
        state="waiting_approval" if gated else "queued"))
    values = {"status": "awaiting_approval" if gated else "dispatched", "project_id": project_id,
              "task_id": prev.task_id, "agent_run_id": run_id}
    if gated:
        values["approval_id"] = create_approval(
            conn, user_id=ins.user_id, kind="action", capability=decision.capability, risk=decision.risk,
            summary=f"{decision.capability} · respuesta", now=now, run_id=run_id, instruction_id=ins.id,
            trace_id=ins.trace_id)
    else:
        enqueue_message(conn, run_id, ins.trace_id)
    audit(conn, user_id=ins.user_id, run_id=run_id, event_type="run.follow_up", subject_type="run", subject_id=run_id,
          trace_id=ins.trace_id, payload={"previous_run_id": str(prev.id), "policy": decision.effect})
    return values


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

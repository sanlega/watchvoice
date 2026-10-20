"""T3 Code execution: pick the machine (runner), deliver a signed webhook, accept progress callbacks.

Each runner is a T3 Code environment (VPS, PC, Mac) with a webhook scheduled task
(`schedule_task` with {type:'webhook', signature:{header:'x-vox-signature', encoding:'hex',
prefix:'sha256='}}). The webhook run is the dispatcher on that machine: it launches a
T3 thread in the right project and reports progress to /v1/runs/{id}/events.
Delivery is at-least-once; the dispatcher dedupes on run_id (see docs/T3CODE_EXECUTION.md).
"""
import hashlib
import hmac
import json
import logging
import os
import re
from datetime import timedelta

from sqlalchemy import select, update

from . import db
from .router import norm

log = logging.getLogger("vox.runners")
CALLBACK_TTL = timedelta(hours=24)
DISPATCH_EVENT = "run.dispatch.v1"
MAX_ATTEMPTS = 8  # shortcut: retried on every worker tick, no backoff; add next_attempt_at if relays flap


def callback_secret() -> bytes:
    secret = os.environ.get("VOX_CALLBACK_SECRET")
    if not secret:
        raise RuntimeError("VOX_CALLBACK_SECRET is not set")
    return secret.encode()


def callback_token(run_id) -> str:
    # Derived, not stored: stable across webhook retries, verifiable without a lookup table.
    return hmac.new(callback_secret(), f"run:{run_id}".encode(), hashlib.sha256).hexdigest()


def verify_callback_token(run_id, token: str) -> bool:
    return hmac.compare_digest(callback_token(run_id), token or "")


def runner_names(r) -> list[str]:
    return [r.name, *(r.aliases or [])]


def select_runner(conn, user_id, text: str, hint: str | None, project_context: dict | None):
    """Returns (runner_row | None, error_code | None). Order: hint > text > project default > user default."""
    runners = conn.execute(select(db.runners).where(db.runners.c.user_id == user_id,
                                                    db.runners.c.enabled.is_(True))).all()
    by_name = {norm(n): r for r in runners for n in runner_names(r)}
    if hint:
        return (by_name[norm(hint)], None) if norm(hint) in by_name else (None, "unknown_runner")
    t = norm(text)
    mentioned = {r.id: r for n, r in by_name.items()
                 if re.search(rf"\b(en|desde|con|on)\s+(el |la |mi |my |the )?{re.escape(n)}\b", t)}
    if len(mentioned) > 1:
        return None, "ambiguous_runner"
    if mentioned:
        return next(iter(mentioned.values())), None
    default_name = (project_context or {}).get("default_runner")
    if default_name and norm(default_name) in by_name:
        return by_name[norm(default_name)], None
    return next((r for r in runners if r.is_default), None), None


def enqueue_dispatch(conn, run_id, trace_id):
    conn.execute(db.outbox_events.insert().values(event_type=DISPATCH_EVENT, aggregate_id=run_id,
                                                  trace_id=trace_id, payload={"run_id": str(run_id)}))


def build_payload(conn, run_id, base_url: str) -> tuple[dict, object]:
    row = conn.execute(
        select(db.agent_runs.c.id, db.agent_runs.c.task_id, db.agent_runs.c.runner_id,
               db.tasks.c.title, db.tasks.c.description, db.tasks.c.intent, db.tasks.c.execution_mode,
               db.tasks.c.instruction_id, db.projects.c.name, db.projects.c.slug, db.projects.c.repo_url)
        .join(db.tasks, db.tasks.c.id == db.agent_runs.c.task_id)
        .join(db.projects, db.projects.c.id == db.tasks.c.project_id)
        .where(db.agent_runs.c.id == run_id)).one()
    runner = conn.execute(select(db.runners).where(db.runners.c.id == row.runner_id)).one()
    payload = {
        "vox": DISPATCH_EVENT, "run_id": str(row.id), "task_id": str(row.task_id),
        "instruction_id": str(row.instruction_id) if row.instruction_id else None,
        "project": {"name": row.name, "slug": row.slug, "repo_url": row.repo_url},
        "title": row.title, "intent": row.intent, "execution_mode": row.execution_mode,
        "text": row.description,  # untrusted user dictation: the dispatcher prompt fences it as data
        "callback": {"url": f"{base_url.rstrip('/')}/v1/runs/{row.id}/events", "token": callback_token(row.id)},
        "sent_at": db.now().isoformat(),
    }
    return payload, runner


def sign(secret: str, raw: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def deliver_dispatches(engine, post, base_url: str | None = None, limit: int = 50) -> int:
    """Outbox relay for run.dispatch.v1. `post(url, content, headers) -> status_code` (injectable for tests)."""
    base_url = base_url or os.environ.get("VOX_PUBLIC_BASE_URL", "")
    delivered = 0
    with engine.connect() as conn:
        pending = conn.execute(select(db.outbox_events).where(
            db.outbox_events.c.event_type == DISPATCH_EVENT, db.outbox_events.c.published_at.is_(None),
            db.outbox_events.c.attempts < MAX_ATTEMPTS).order_by(db.outbox_events.c.created_at).limit(limit)).all()
    for ev in pending:
        with engine.begin() as conn:
            payload, runner = build_payload(conn, ev.aggregate_id, base_url)
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        secret = os.environ.get(runner.secret_env)
        error, status = None, None
        if not secret:
            error = f"missing secret env {runner.secret_env}"
        else:
            try:
                status = post(runner.webhook_url, raw, {"Content-Type": "application/json",
                                                        "X-Vox-Signature": sign(secret, raw),
                                                        "X-Vox-Delivery": str(ev.id)})
                if not 200 <= status < 300:
                    error = f"http {status}"
            except Exception as exc:  # network errors: retry on next drain
                error = type(exc).__name__
        with engine.begin() as conn:
            now = db.now()
            values = {"attempts": ev.attempts + 1, "last_error": error}
            if error is None:
                values["published_at"] = now
                conn.execute(update(db.agent_runs).where(db.agent_runs.c.id == ev.aggregate_id,
                                                         db.agent_runs.c.state == "queued")
                             .values(state="provisioning", last_event_at=now,
                                     callback_expires_at=now + CALLBACK_TTL))
                delivered += 1
            conn.execute(update(db.outbox_events).where(db.outbox_events.c.id == ev.id).values(**values))
        log.info("run_dispatch", extra={"status": "delivered" if error is None else "retry",
                                        "code": error, "instruction_id": payload["instruction_id"]})
    return delivered


def httpx_post(url, content, headers) -> int:
    import httpx
    return httpx.post(url, content=content, headers=headers, timeout=15).status_code

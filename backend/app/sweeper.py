"""Run timeouts (ADR-0001 §9): stale notice after 30 min of silence (once), timed_out past the deadline."""
from datetime import timedelta

from sqlalchemy import select, update

from . import db, notify
from .control import cancel_run
from .processor import audit, aware

STALE_AFTER = timedelta(minutes=30)


def sweep(engine, now=None) -> dict:
    now = now or db.now()
    counts = {"stale": 0, "timed_out": 0}
    with engine.begin() as conn:
        runs = conn.execute(select(db.agent_runs).where(db.agent_runs.c.state.in_(["provisioning", "running"]))).all()
        for run in runs:
            if run.deadline_at and aware(run.deadline_at) <= now:
                cancel_run(conn, run, now, reason="timed_out")
                audit(conn, user_id=run.user_id, run_id=run.id, event_type="run.timed_out", subject_type="run",
                      subject_id=run.id)
                notify.enqueue(conn, run.user_id, "run.timed_out", "active", "VOX: tiempo agotado",
                               "La tarea superó su plazo y se canceló", run_id=run.id)
                counts["timed_out"] += 1
            elif (run.last_event_at and aware(run.last_event_at) <= now - STALE_AFTER
                  and run.stale_notified_at is None):
                conn.execute(update(db.agent_runs).where(db.agent_runs.c.id == run.id).values(stale_notified_at=now))
                notify.enqueue(conn, run.user_id, "run.stale", "passive", "VOX: sin noticias",
                               "La tarea lleva 30 min sin informar", run_id=run.id)
                counts["stale"] += 1
    return counts

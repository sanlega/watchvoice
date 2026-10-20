"""Worker loop: route instructions, deliver runner webhooks, sweep timeouts, send pushes.
Env: VOX_DATABASE_URL, VOX_PUBLIC_BASE_URL, VOX_CALLBACK_SECRET, VOX_RUNNER_SECRET_*, and optionally
VOX_APNS_TEAM_ID / VOX_APNS_KEY_ID / VOX_APNS_KEY_PATH / VOX_APNS_TOPIC (no push without them)."""
import logging
import os
import time

from . import db, notify
from .processor import drain_outbox
from .runners import deliver_dispatches, httpx_post
from .sweeper import sweep

if __name__ == "__main__":
    engine = db.make_engine(os.environ["VOX_DATABASE_URL"])
    sender = notify.ApnsSender() if os.environ.get("VOX_APNS_KEY_PATH") else None
    if sender is None:
        logging.getLogger("vox.worker").warning("APNs not configured: watch notifications disabled")
    last_sweep = 0.0
    while True:
        busy = drain_outbox(engine) + deliver_dispatches(engine, httpx_post)
        if sender:
            busy += notify.deliver(engine, sender)
        if time.time() - last_sweep > 60:
            sweep(engine)
            last_sweep = time.time()
        time.sleep(0.5 if busy else 3)

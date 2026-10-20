"""Worker loop: route queued instructions, then deliver run dispatches to T3 Code runners.
`VOX_DATABASE_URL=... VOX_PUBLIC_BASE_URL=https://vox.example VOX_CALLBACK_SECRET=... python -m app.worker`"""
import os
import time

from . import db
from .processor import drain_outbox
from .runners import deliver_dispatches, httpx_post

if __name__ == "__main__":
    engine = db.make_engine(os.environ["VOX_DATABASE_URL"])
    while True:
        busy = drain_outbox(engine) + deliver_dispatches(engine, httpx_post)
        time.sleep(0.5 if busy else 3)

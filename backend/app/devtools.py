"""Local dev bootstrap (SQLite or an empty dev DB): `python -m app.devtools sqlite:///./vox-dev.db`.
Prints a one-time watch token; only its SHA-256 is stored."""
import secrets
import sys
import uuid

from . import db
from .control import WATCH_SCOPES
from .processor import sha256


def seed(engine, email="owner@example.invalid"):
    db.metadata.create_all(engine)
    user_id, token = uuid.uuid4(), "vox_" + secrets.token_urlsafe(32)
    with engine.begin() as conn:
        conn.execute(db.users.insert().values(id=user_id, email=email))
        conn.execute(db.devices.insert().values(user_id=user_id, name="Apple Watch", hardware_model="apple_watch",
                                                credential_hash=sha256(token), scopes=WATCH_SCOPES))
    return user_id, token


if __name__ == "__main__":
    _, tok = seed(db.make_engine(sys.argv[1] if len(sys.argv) > 1 else "sqlite:///./vox-dev.db"))
    print(tok)

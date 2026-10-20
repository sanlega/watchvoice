import base64
import os
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db
from app.devtools import seed
from app.main import create_app
from app.devicesig import canonical
from app.processor import sha256


def new_device_key():
    key = ec.generate_private_key(ec.SECP256R1())
    raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return key, base64.b64encode(raw).decode()


def sign(key, message: dict) -> str:
    return base64.b64encode(key.sign(canonical(message), ec.ECDSA(hashes.SHA256()))).decode()


REPO = Path(__file__).resolve().parents[2]
MIGRATIONS = [REPO / "docs/vox-agent-spec/schema.sql",
              *sorted(p for p in (REPO / "backend/migrations").glob("*.sql") if not p.name.endswith(".down.sql"))]


def database_url(tmp_path) -> str:
    """SQLite by default. With VOX_TEST_PG=postgresql://user:pw@host:port, each test gets a fresh PostgreSQL
    database built from the real migrations (001 + 00N), so code and DDL are checked against each other."""
    pg = os.environ.get("VOX_TEST_PG")
    if not pg:
        return f"sqlite:///{tmp_path / 'vox.db'}"
    import psycopg
    name = "t_" + uuid.uuid4().hex[:12]
    with psycopg.connect(f"{pg}/postgres", autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    with psycopg.connect(f"{pg}/{name}", autocommit=True) as conn:
        for f in MIGRATIONS:
            conn.execute(f.read_text("utf-8"))
    return f"{pg.replace('postgresql://', 'postgresql+psycopg://')}/{name}"


class Env:
    def __init__(self, tmp_path, **app_kw):
        self.app = create_app(database_url(tmp_path), **app_kw)
        self.engine = self.app.state.engine
        self.url = self.engine.url.render_as_string(hide_password=False)
        self.user_id, self.token = seed(self.engine)
        self.key, public_key = new_device_key()
        with self.engine.begin() as c:
            c.execute(db.devices.update().where(db.devices.c.user_id == self.user_id).values(public_key=public_key))
            self.device_id = c.execute(select(db.devices.c.id).where(db.devices.c.user_id == self.user_id)).scalar_one()
        self.client = TestClient(self.app)
        self.musiquillas, self.hardware = uuid.uuid4(), uuid.uuid4()
        with self.engine.begin() as c:
            c.execute(db.projects.insert(), [
                dict(id=self.musiquillas, user_id=self.user_id, slug="musiquillas", name="Musiquillas",
                     description="App de juegos musicales para amigos"),
                dict(id=self.hardware, user_id=self.user_id, slug="hardware-vox", name="Hardware VOX",
                     description="Dispositivo ESP32 LTE para dictar instrucciones")])
            c.execute(db.project_aliases.insert().values(project_id=self.musiquillas, alias="musiquilla",
                                                         alias_norm="musiquilla"))

    def headers(self, key=None, token=None):
        return {"Authorization": f"Bearer {token or self.token}", "Idempotency-Key": str(key or uuid.uuid4())}

    def ts(self, delta_s=0):
        from datetime import timedelta
        return (db.now() + timedelta(seconds=delta_s)).isoformat()

    def decide(self, ap, decision="approved", key=None, ts=None, fingerprint=None, token=None, device_id=None):
        fp = fingerprint or ap["action_fingerprint"]
        ts = ts or self.ts()
        msg = {"approval_id": ap["id"], "action_fingerprint": fp, "decision": decision,
               "device_id": str(device_id or self.device_id), "ts": ts}
        body = {"decision": decision, "action_fingerprint": fp, "ts": ts, "signature": sign(key or self.key, msg)}
        return self.client.post(f"/v1/approvals/{ap['id']}/decision", json=body, headers=self.headers(token=token))

    def signed(self, path, action, extra=None, key=None, **msg_extra):
        ts = self.ts()
        msg = {"action": action, "device_id": str(self.device_id), "ts": ts, **msg_extra}
        return self.client.post(path, json={"ts": ts, "signature": sign(key or self.key, msg), **(extra or {})},
                                headers=self.headers())

    def pending(self):
        return self.client.get("/v1/approvals?status=pending", headers=self.headers()).json()

    def add_device(self, scopes, revoked=False):
        token = "vox_" + uuid.uuid4().hex
        with self.engine.begin() as c:
            c.execute(db.devices.insert().values(user_id=self.user_id, name="d", hardware_model="esp32-s3",
                                                 credential_hash=sha256(token), scopes=scopes,
                                                 revoked_at=db.now() if revoked else None))
        return token

    def body(self, text="En Musiquillas añade modo multijugador", cid=None, **extra):
        return {"client_instruction_id": str(cid or uuid.uuid4()), "source": "apple_watch", "text": text,
                "language": "es", "project_hint": None,
                "transcription": {"engine": "mock", "model_version": None, "on_device": True, "duration_ms": 7400},
                "client": {"app_version": "0.1.0", "platform": "watchos"}, **extra}

    def submit(self, text="En Musiquillas añade modo multijugador", key=None, **kw):
        return self.client.post("/v1/instructions", json=self.body(text, **kw), headers=self.headers(key))

    def get(self, instruction_id):
        return self.client.get(f"/v1/instructions/{instruction_id}", headers=self.headers()).json()

    def count(self, table, *where):
        from sqlalchemy import func, select
        with self.engine.connect() as c:
            return c.execute(select(func.count()).select_from(table).where(*where)).scalar_one()


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


SECRET = "test-webhook-secret"


@pytest.fixture
def renv(env, monkeypatch):
    monkeypatch.setenv("VOX_CALLBACK_SECRET", "test-callback-secret")
    monkeypatch.setenv("VOX_RUNNER_SECRET_MAC", SECRET)
    monkeypatch.setenv("VOX_RUNNER_SECRET_VPS", SECRET)
    with env.engine.begin() as c:
        c.execute(db.runners.insert(), [
            dict(user_id=env.user_id, name="vps", aliases=["servidor"], webhook_url="https://relay.example/vps",
                 secret_env="VOX_RUNNER_SECRET_VPS", is_default=True),
            dict(user_id=env.user_id, name="mac", aliases=["macbook"], webhook_url="https://relay.example/mac",
                 secret_env="VOX_RUNNER_SECRET_MAC", is_default=False)])
    return env

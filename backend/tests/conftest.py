import uuid

import pytest
from fastapi.testclient import TestClient

from app import db
from app.devtools import seed
from app.main import create_app
from app.processor import sha256


class Env:
    def __init__(self, tmp_path, **app_kw):
        self.app = create_app(f"sqlite:///{tmp_path / 'vox.db'}", **app_kw)
        self.engine = self.app.state.engine
        self.user_id, self.token = seed(self.engine)
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

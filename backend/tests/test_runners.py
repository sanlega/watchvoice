import hashlib
import hmac
import json
import uuid

from sqlalchemy import select

from app import db
from app.runners import DISPATCH_EVENT, deliver_dispatches

from conftest import SECRET


class FakeRelay:
    def __init__(self, statuses=(200,)):
        self.statuses, self.calls = list(statuses), []

    def __call__(self, url, content, headers):
        self.calls.append((url, content, headers))
        status = self.statuses.pop(0) if self.statuses else 200
        if isinstance(status, Exception):
            raise status
        return status


def test_text_selects_runner_and_dispatch_is_signed(renv):
    iid = renv.submit("En Musiquillas añade modo multijugador en el mac").json()["instruction_id"]
    got = renv.get(iid)
    assert got["status"] == "dispatched" and got["progress"]["runner"] == "mac"
    assert got["progress"]["run_state"] == "queued"
    relay = FakeRelay()
    assert deliver_dispatches(renv.engine, relay, base_url="https://vox.example") == 1
    url, raw, headers = relay.calls[0]
    assert url == "https://relay.example/mac"
    assert headers["X-Vox-Signature"] == "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    payload = json.loads(raw)
    assert payload["vox"] == DISPATCH_EVENT and payload["project"]["name"] == "Musiquillas"
    assert payload["callback"]["url"] == f"https://vox.example/v1/runs/{got['agent_run_id']}/events"
    assert renv.get(iid)["progress"]["run_state"] == "provisioning"
    assert deliver_dispatches(renv.engine, relay) == 0  # delivered once


def test_default_runner_and_hint(renv):
    default = renv.get(renv.submit().json()["instruction_id"])
    assert default["progress"]["runner"] == "vps"
    hinted = renv.get(renv.submit(runner_hint="macbook").json()["instruction_id"])
    assert hinted["progress"]["runner"] == "mac"


def test_unknown_or_ambiguous_runner_asks_instead_of_running(renv):
    unknown = renv.get(renv.submit(runner_hint="raspberry").json()["instruction_id"])
    assert unknown["status"] == "needs_clarification" and unknown["error_code"] == "unknown_runner"
    both = renv.get(renv.submit("En Musiquillas añade login en el mac y en el vps").json()["instruction_id"])
    assert both["status"] == "needs_clarification" and both["error_code"] == "ambiguous_runner"
    assert renv.count(db.tasks) == 0


def test_no_runner_configured_keeps_task_unassigned(env):
    got = env.get(env.submit().json()["instruction_id"])
    assert got["status"] == "dispatched" and got["progress"]["runner"] is None
    assert env.count(db.outbox_events, db.outbox_events.c.event_type == DISPATCH_EVENT) == 0


def test_gated_run_is_dispatched_only_after_approval(renv):
    renv.submit("En Musiquillas despliega a producción en el mac")
    relay = FakeRelay()
    assert deliver_dispatches(renv.engine, relay) == 0
    assert renv.decide(renv.pending()[0]).status_code == 200
    assert deliver_dispatches(renv.engine, relay) == 1 and relay.calls[0][0] == "https://relay.example/mac"


def test_failed_delivery_is_retried_then_succeeds(renv, monkeypatch):
    renv.submit()
    relay = FakeRelay([500, ConnectionError("down"), 202])
    assert deliver_dispatches(renv.engine, relay) == 0
    assert deliver_dispatches(renv.engine, relay) == 0
    assert deliver_dispatches(renv.engine, relay) == 1
    with renv.engine.connect() as c:
        ev = c.execute(select(db.outbox_events).where(db.outbox_events.c.event_type == DISPATCH_EVENT)).one()
    assert ev.attempts == 3 and ev.published_at is not None
    monkeypatch.delenv("VOX_RUNNER_SECRET_VPS")
    renv.submit("En Musiquillas añade chat")
    assert deliver_dispatches(renv.engine, FakeRelay()) == 0  # never sends unsigned


def test_progress_callbacks_update_task_and_watch_view(renv):
    iid = renv.submit().json()["instruction_id"]
    relay = FakeRelay()
    deliver_dispatches(renv.engine, relay, base_url="https://vox.example")
    payload = json.loads(relay.calls[0][1])
    run_id, token = payload["run_id"], payload["callback"]["token"]
    url_events = f"/v1/runs/{run_id}/events"
    post = lambda body, tok=token: renv.client.post(url_events, json=body, headers={"Authorization": f"Bearer {tok}"})

    assert post({"event": "started"}, tok="forged").status_code == 401
    assert post({"event": "started", "external_ref": "thread-123"}).status_code == 204
    assert renv.get(iid)["progress"]["run_state"] == "running"
    assert post({"event": "progress", "summary": "Tests en verde, abriendo PR"}).status_code == 204
    assert post({"event": "succeeded", "pr_url": "http://insecure"}).status_code == 422
    assert post({"event": "succeeded", "summary": "PR abierta",
                 "pr_url": "https://github.com/sanlega/musiquillas/pull/7"}).status_code == 204
    prog = renv.get(iid)["progress"]
    assert prog["run_state"] == "succeeded" and prog["pr_url"].endswith("/pull/7") and prog["summary"] == "PR abierta"
    with renv.engine.connect() as c:
        assert c.execute(select(db.tasks.c.state)).scalar_one() == "completed"
    assert post({"event": "progress"}).status_code == 409  # terminal is immutable


def test_succeeded_result_is_exposed_but_never_logged_or_audited(renv, caplog):
    import logging
    caplog.set_level(logging.DEBUG, logger="vox")
    iid = renv.submit().json()["instruction_id"]
    relay = FakeRelay()
    deliver_dispatches(renv.engine, relay, base_url="https://vox.example")
    payload = json.loads(relay.calls[0][1])
    post = lambda body: renv.client.post(f"/v1/runs/{payload['run_id']}/events", json=body,
                                         headers={"Authorization": f"Bearer {payload['callback']['token']}"})
    assert renv.get(iid)["progress"]["result"] is None
    assert post({"event": "succeeded", "result": "x" * 4001}).status_code == 422
    result = "Causa: token 9137 caducado. " + "a" * 3970
    assert len(result) <= 4000
    assert post({"event": "succeeded", "summary": "Diagnóstico listo", "result": result}).status_code == 204
    assert renv.get(iid)["progress"]["result"] == result
    assert caplog.records and all("9137" not in r.getMessage() and "9137" not in str(r.__dict__) for r in caplog.records)
    with renv.engine.connect() as c:
        assert all("9137" not in json.dumps(p, default=str) for p in c.execute(select(db.audit_events.c.payload)).scalars())


def test_callback_rejected_before_dispatch(renv):
    iid = renv.submit().json()["instruction_id"]
    run_id = renv.get(iid)["agent_run_id"]
    from app.runners import callback_token
    r = renv.client.post(f"/v1/runs/{run_id}/events", json={"event": "started"},
                         headers={"Authorization": f"Bearer {callback_token(uuid.UUID(run_id))}"})
    assert r.status_code == 401

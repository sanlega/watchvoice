"""ADR-0001: rules, bypass, voice policy changes, agent requests, cancel/pause, timeouts, push, pairing."""
import base64
import json
import uuid
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select, update

from app import db, notify
from app.admin import main as admin
from app.policy import evaluate
from app.processor import aware
from app.runners import CANCEL_EVENT, DISPATCH_EVENT, deliver_dispatches
from app.sweeper import sweep
from conftest import new_device_key
from test_runners import FakeRelay

NOW = db.now()


def rule(mode, runner=None, project=None, effects=None, expires=None, revoked=None):
    return {"id": uuid.uuid4(), "runner_id": runner, "project_id": project, "mode": mode, "effects": effects or {},
            "expires_at": expires, "revoked_at": revoked}


# ------------------------------------------------------------ pure policy

def test_default_is_granular_and_asks_for_high_risk():
    d = evaluate({"deploy.production"}, [], "vps", "p", NOW)
    assert (d.effect, d.risk, d.runtime_mode) == ("ask", "high", "auto")
    assert evaluate({"repo.write_branch"}, [], "vps", "p", NOW).effect == "allow"


def test_most_specific_wins_and_ties_are_restrictive():
    rules = [rule("bypass", runner="vps"), rule("granular", runner="vps", project="p", effects={"data.delete": "deny"})]
    assert evaluate({"data.delete"}, rules, "vps", "p", NOW).effect == "deny"
    assert evaluate({"deploy.production"}, rules, "vps", "p", NOW).effect == "allow"  # falls through to runner bypass
    assert evaluate({"deploy.production"}, rules, "vps", "other", NOW).runtime_mode == "full-access"
    tie = [rule("bypass", runner="vps"), rule("granular", runner="vps", effects={"deploy.production": "ask"})]
    d = evaluate({"deploy.production"}, tie, "vps", "p", NOW)
    assert d.effect == "ask" and d.runtime_mode == "auto"


def test_floor_is_denied_even_in_bypass_and_expired_rules_ignored():
    assert evaluate({"backup.delete"}, [rule("bypass")], "vps", "p", NOW).effect == "deny"
    assert evaluate({"secrets.exfiltrate"}, [rule("bypass")], None, None, NOW).effect == "deny"
    old = [rule("bypass", expires=NOW - timedelta(seconds=1)), rule("bypass", revoked=NOW)]
    assert evaluate({"deploy.production"}, old, "vps", "p", NOW).effect == "ask"


# ------------------------------------------------------------ voice policy changes

def test_voice_bypass_needs_tap_then_applies_with_expiry(renv):
    got = renv.get(renv.submit("En el vps sáltate los permisos durante 2 horas").json()["instruction_id"])
    assert got["status"] == "awaiting_approval" and got["intent"] == "policy_change"
    assert renv.count(db.policy_rules) == 0
    ap = renv.pending()[0]
    assert ap["kind"] == "policy_change" and ap["action_summary"] == "Bypass · vps · 2 h" and ap["runner"] == "vps"
    assert renv.decide(ap).status_code == 200
    with renv.engine.connect() as c:
        r = c.execute(select(db.policy_rules)).one()
        assert c.execute(select(db.instructions.c.status).where(
            db.instructions.c.id == uuid.UUID(got["id"]))).scalar_one() == "applied"
    assert r.mode == "bypass" and r.runner_id is not None and r.project_id is None
    assert timedelta(hours=1, minutes=59) < aware(r.expires_at) - db.now() <= timedelta(hours=2)
    # Now a production deploy on the VPS goes straight through, launched full-access.
    deploy = renv.get(renv.submit("En Musiquillas despliega a producción en el vps").json()["instruction_id"])
    assert deploy["status"] == "dispatched"
    relay = FakeRelay()
    deliver_dispatches(renv.engine, relay)
    assert json.loads(relay.calls[0][1])["runtime_mode"] == "full-access"
    # ...but not on the Mac.
    mac = renv.get(renv.submit("En Musiquillas despliega a producción en el mac").json()["instruction_id"])
    assert mac["status"] == "awaiting_approval"


@pytest.mark.parametrize("text,code", [
    ("No te saltes los permisos en el vps", "policy_ambiguous"),
    ("Sáltate los permisos durante una hora", "policy_scope_missing"),
    ("Sáltate todos los permisos durante una hora", "policy_scope_missing"),  # "todos los permisos" is not a scope
    ("En el vps sáltate los permisos", "policy_duration_missing"),
    ("En el vps y en el mac sáltate los permisos durante una hora", "policy_scope_ambiguous"),
])
def test_voice_loosen_ambiguity_asks(renv, text, code):
    got = renv.get(renv.submit(text).json()["instruction_id"])
    assert got["status"] == "needs_clarification" and got["error_code"] == code
    assert renv.count(db.approvals) == 0 and renv.count(db.policy_rules) == 0


def test_voice_tighten_applies_immediately(renv):
    with renv.engine.begin() as c:
        vps = c.execute(select(db.runners.c.id).where(db.runners.c.name == "vps")).scalar_one()
    admin(["--db", renv.url, "rule-add", "--email", "owner@example.invalid", "--runner", "vps",
           "--mode", "bypass"])
    got = renv.get(renv.submit("En el vps pídeme permiso para todo").json()["instruction_id"])
    assert got["status"] == "applied" and renv.count(db.approvals) == 0
    with renv.engine.connect() as c:
        rules = [r._asdict() for r in c.execute(select(db.policy_rules))]
    assert {r["mode"] for r in rules} == {"bypass", "granular"} and all(r["runner_id"] == vps for r in rules)
    # Same specificity: granular beats bypass, so even branch writes now ask.
    assert renv.get(renv.submit("En Musiquillas añade login en el vps").json()["instruction_id"])["status"] == "dispatched"
    assert renv.get(renv.submit("En Musiquillas despliega a producción en el vps").json()["instruction_id"])["status"] \
        == "awaiting_approval"


def test_deny_rule_fails_instruction_without_task(renv):
    admin(["--db", renv.url, "rule-add", "--email", "owner@example.invalid", "--project", "musiquillas",
           "--mode", "granular", "--effect", "data.delete=deny"])
    got = renv.get(renv.submit("En Musiquillas borra la base de datos").json()["instruction_id"])
    assert got["status"] == "failed" and got["error_code"] == "policy_denied" and renv.count(db.tasks) == 0


def test_admin_cli_refuses_to_lift_floor(renv):
    with pytest.raises(SystemExit):
        admin(["--db", renv.url, "rule-add", "--email", "owner@example.invalid", "--mode", "granular",
               "--effect", "backup.delete=allow"])


# ------------------------------------------------------------ agent approval requests

def _dispatched_run(env, text="En Musiquillas añade login en el vps"):
    env.submit(text)
    relay = FakeRelay()
    deliver_dispatches(env.engine, relay, base_url="https://vox.example")
    p = json.loads(relay.calls[-1][1])
    return p["run_id"], {"Authorization": f"Bearer {p['callback']['token']}"}


def test_agent_request_pending_then_decided_by_watch(renv):
    run_id, auth = _dispatched_run(renv)
    url = f"/v1/runs/{run_id}/approval-requests"
    r = renv.client.post(url, json={"capability": "repo.merge_protected",
                                    "action_summary": "Merge **PR #7** a main‮", "details": {"pr": 7}}, headers=auth)
    assert r.status_code == 201 and r.json()["state"] == "pending"
    ap = renv.pending()[0]
    assert ap["kind"] == "agent_request" and ap["agent_provided"] and ap["action_summary"] == "Merge PR 7 a main"
    assert renv.decide(ap).status_code == 200
    assert renv.client.get(f"{url}/{ap['id']}", headers=auth).json()["state"] == "approved"


def test_agent_request_in_bypass_is_immediate_and_quota_enforced(renv):
    run_id, auth = _dispatched_run(renv)
    url = f"/v1/runs/{run_id}/approval-requests"
    for _ in range(3):
        assert renv.client.post(url, json={"capability": "deploy.production", "action_summary": "deploy"},
                                headers=auth).status_code == 201
    assert renv.client.post(url, json={"capability": "deploy.production", "action_summary": "deploy"},
                            headers=auth).status_code == 429
    admin(["--db", renv.url, "rule-add", "--email", "owner@example.invalid", "--runner", "vps",
           "--mode", "bypass"])
    r = renv.client.post(url, json={"capability": "deploy.production", "action_summary": "deploy"}, headers=auth)
    assert r.json() == {"approval_id": None, "state": "approved", "expires_at": None}
    floor = renv.client.post(url, json={"capability": "backup.delete", "action_summary": "x"}, headers=auth)
    assert floor.json()["state"] == "denied"


# ------------------------------------------------------------ cancel / pause / timeouts

def test_signed_cancel_sends_expiring_cancel_webhook(renv):
    run_id, auth = _dispatched_run(renv)
    renv.client.post(f"/v1/runs/{run_id}/events", json={"event": "started", "external_ref": "thread-9"}, headers=auth)
    assert renv.signed(f"/v1/runs/{run_id}/cancel", "cancel_run", key=new_device_key()[0], run_id=run_id) \
        .status_code == 403
    assert renv.signed(f"/v1/runs/{run_id}/cancel", "cancel_run", run_id=run_id).status_code == 202
    relay = FakeRelay()
    assert deliver_dispatches(renv.engine, relay) == 1
    payload = json.loads(relay.calls[0][1])
    assert payload["vox"] == CANCEL_EVENT and payload["external_ref"] == "thread-9"
    late = renv.client.post(f"/v1/runs/{run_id}/events", json={"event": "succeeded"}, headers=auth)
    assert late.status_code == 409


def test_cancel_webhook_expires(renv):
    run_id, auth = _dispatched_run(renv)
    renv.signed(f"/v1/runs/{run_id}/cancel", "cancel_run", run_id=run_id)
    with renv.engine.begin() as c:
        ev = c.execute(select(db.outbox_events).where(db.outbox_events.c.event_type == CANCEL_EVENT)).one()
        c.execute(update(db.outbox_events).where(db.outbox_events.c.id == ev.id).values(
            payload=ev.payload | {"expires_at": (db.now() - timedelta(seconds=1)).isoformat()}))
    assert deliver_dispatches(renv.engine, FakeRelay()) == 0


def test_pause_cancels_active_and_holds_new_dispatch(renv):
    _dispatched_run(renv)
    r = renv.signed("/v1/pause", "pause", extra={"paused": True})
    assert r.json() == {"paused": True, "cancelled_runs": 1}
    renv.submit("En Musiquillas añade chat en el vps")
    relay = FakeRelay()
    assert deliver_dispatches(renv.engine, relay) == 1 and json.loads(relay.calls[0][1])["vox"] == CANCEL_EVENT
    assert deliver_dispatches(renv.engine, relay) == 0  # new dispatch held
    renv.signed("/v1/pause", "resume", extra={"paused": False})
    assert deliver_dispatches(renv.engine, relay) == 1 and json.loads(relay.calls[-1][1])["vox"] == DISPATCH_EVENT


def test_sweeper_stale_once_then_timeout(renv):
    run_id, auth = _dispatched_run(renv)
    later = db.now() + timedelta(minutes=31)
    assert sweep(renv.engine, later) == {"stale": 1, "timed_out": 0}
    assert sweep(renv.engine, later) == {"stale": 0, "timed_out": 0}
    assert sweep(renv.engine, db.now() + timedelta(hours=3)) == {"stale": 0, "timed_out": 1}
    with renv.engine.connect() as c:
        assert c.execute(select(db.agent_runs.c.state)).scalar_one() == "timed_out"
        kinds = [e.payload["kind"] for e in c.execute(select(db.outbox_events).where(
            db.outbox_events.c.event_type == notify.EVENT))]
    assert kinds == ["run.stale", "run.timed_out"]


# ------------------------------------------------------------ push

class FakeApns:
    def __init__(self, status=200):
        self.status, self.calls = status, []

    def __call__(self, token, env, payload, headers):
        self.calls.append((token, env, payload, headers))
        return self.status


def test_push_payload_has_no_secrets_and_410_forgets_token(renv):
    renv.client.put("/v1/devices/me/push-token", json={"token": "ab" * 32, "environment": "development"},
                    headers=renv.headers())
    renv.submit("En Musiquillas despliega a producción en el vps")
    apns = FakeApns()
    assert notify.deliver(renv.engine, apns) == 1
    token, env_, payload, headers = apns.calls[0]
    ap = renv.pending()[0]
    assert env_ == "development" and payload["aps"]["category"] == "VOX_APPROVAL_HIGH"
    assert payload["aps"]["interruption-level"] == "time-sensitive"
    assert payload["vox"] == {"kind": "approval", "approval_id": ap["id"], "run_id": ap["run_id"],
                              "action_fingerprint": ap["action_fingerprint"]}
    assert headers["apns-collapse-id"] == ap["id"] and headers["apns-push-type"] == "alert"
    assert renv.token not in json.dumps(payload)
    renv.submit("En Musiquillas despliega a producción en el mac")
    assert notify.deliver(renv.engine, FakeApns(410)) == 0
    with renv.engine.connect() as c:
        assert c.execute(select(db.devices.c.apns_token).where(db.devices.c.id == renv.device_id)).scalar() is None


def test_apns_jwt_is_valid_es256():
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    sender = notify.ApnsSender(team_id="TEAM123456", key_id="KEY1234567", key_pem=pem, topic="app.vox.VoxWatch")
    head, claims, sig = sender.jwt().split(".")
    pad = lambda x: base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))
    assert json.loads(pad(head)) == {"alg": "ES256", "kid": "KEY1234567"}
    assert json.loads(pad(claims))["iss"] == "TEAM123456"
    raw = pad(sig)
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key.public_key().verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))


# ------------------------------------------------------------ pairing

def test_pairing_code_single_use_and_rate_limited(renv, capsys):
    admin(["--db", renv.url, "pair", "--email", "owner@example.invalid", "--name", "Watch Ultra"])
    code = capsys.readouterr().out.split()[0]
    assert len(code) == 8 and not set(code) & set("01OIL")
    _, pub = new_device_key()
    r = renv.client.post("/v1/devices/pair", json={"code": code, "public_key": pub, "name": "Watch Ultra"})
    assert r.status_code == 201 and r.json()["token"].startswith("vox_")
    paired = r.json()["token"]
    assert renv.client.get("/v1/approvals", headers=renv.headers(token=paired)).status_code == 200
    again = renv.client.post("/v1/devices/pair", json={"code": code, "public_key": pub, "name": "x"})
    assert again.status_code == 401
    bad = renv.client.post("/v1/devices/pair", json={"code": code, "public_key": "bm90LWEta2V5", "name": "x"})
    assert bad.status_code == 422
    for _ in range(5):
        renv.client.post("/v1/devices/pair", json={"code": "ZZZZZZZZ", "public_key": pub, "name": "x"})
    assert renv.client.post("/v1/devices/pair", json={"code": "ZZZZZZZZ", "public_key": pub, "name": "x"}) \
        .status_code == 429


def test_approval_fixtures_match_models():
    from pathlib import Path
    from app.schemas import Approval, ApprovalDecision
    root = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "approvals"
    for name in ["approval.pending.json", "approval.policy_change.json"]:
        Approval.model_validate(json.loads((root / name).read_text("utf-8")))
    ApprovalDecision.model_validate(json.loads((root / "decision.request.json").read_text("utf-8")))

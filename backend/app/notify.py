"""Watch notifications (ADR-0001 §6): outbox `notify.v1` -> APNs, sent directly to the watch.

Payloads never carry secrets; lock-screen text is short and generic.
Requires a paid Apple Developer Program membership (APNs key + Push capability).
"""
import base64
import json
import logging
import os
import time
import uuid
from datetime import timedelta

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from sqlalchemy import select, update

from . import db

log = logging.getLogger("vox.notify")
EVENT = "notify.v1"
MAX_ATTEMPTS = 5
LEVELS = {"time-sensitive", "active", "passive"}


def enqueue(conn, user_id, kind: str, level: str, title: str, body: str, *, category: str | None = None,
            approval_id=None, run_id=None, fingerprint: str | None = None, expires_at=None):
    assert level in LEVELS
    conn.execute(db.outbox_events.insert().values(
        event_type=EVENT, aggregate_id=approval_id or run_id or user_id, trace_id=run_id or user_id,
        payload={"user_id": str(user_id), "kind": kind, "level": level, "title": title[:60], "body": body[:120],
                 "category": category, "approval_id": str(approval_id) if approval_id else None,
                 "run_id": str(run_id) if run_id else None, "action_fingerprint": fingerprint,
                 "expires_at": expires_at.isoformat() if expires_at else None}))


def apns_request(p: dict) -> tuple[dict, dict]:
    aps = {"alert": {"title": p["title"], "body": p["body"]}, "sound": "default", "interruption-level": p["level"]}
    if p["category"]:
        aps["category"] = p["category"]
    payload = {"aps": aps, "vox": {k: p[k] for k in ("kind", "approval_id", "run_id", "action_fingerprint") if p[k]}}
    headers = {"apns-push-type": "alert", "apns-priority": "5" if p["level"] == "passive" else "10",
               "apns-expiration": str(int(db.parse_ts(p["expires_at"]).timestamp())) if p["expires_at"] else "0"}
    if p["approval_id"] or p["run_id"]:
        headers["apns-collapse-id"] = (p["approval_id"] or p["run_id"])[:64]
    return payload, headers


def deliver(engine, send, limit: int = 50) -> int:
    """`send(device_token, env, payload, headers) -> status_code` (injectable; ApnsSender in prod)."""
    sent = 0
    with engine.connect() as conn:
        events = conn.execute(select(db.outbox_events).where(
            db.outbox_events.c.event_type == EVENT, db.outbox_events.c.published_at.is_(None),
            db.outbox_events.c.attempts < MAX_ATTEMPTS).order_by(db.outbox_events.c.created_at).limit(limit)).all()
    for ev in events:
        p = ev.payload
        with engine.connect() as conn:
            devices = conn.execute(select(db.devices).where(
                db.devices.c.user_id == uuid.UUID(p["user_id"]),
                db.devices.c.revoked_at.is_(None), db.devices.c.apns_token.is_not(None))).all()
        payload, headers = apns_request(p)
        error = None
        for d in devices:
            try:
                status = send(d.apns_token, d.apns_env or "production", payload, headers)
            except Exception as exc:
                error = type(exc).__name__
                continue
            if status == 410:  # Unregistered: forget the token
                with engine.begin() as conn:
                    conn.execute(update(db.devices).where(db.devices.c.id == d.id).values(apns_token=None))
            elif status >= 500 or status == 429:
                error = f"http {status}"
            else:
                sent += status == 200
        with engine.begin() as conn:
            conn.execute(update(db.outbox_events).where(db.outbox_events.c.id == ev.id).values(
                attempts=ev.attempts + 1, last_error=error, published_at=None if error else db.now()))
        log.info("notify", extra={"status": "sent" if error is None else "retry", "code": p["kind"]})
    return sent


class ApnsSender:
    """Token-based APNs over HTTP/2. Env: VOX_APNS_TEAM_ID, VOX_APNS_KEY_ID, VOX_APNS_KEY_PATH (.p8), VOX_APNS_TOPIC."""
    HOSTS = {"production": "https://api.push.apple.com", "development": "https://api.sandbox.push.apple.com"}

    def __init__(self, team_id=None, key_id=None, key_pem: bytes | None = None, topic=None):
        import httpx
        self.team_id = team_id or os.environ["VOX_APNS_TEAM_ID"]
        self.key_id = key_id or os.environ["VOX_APNS_KEY_ID"]
        self.topic = topic or os.environ["VOX_APNS_TOPIC"]
        pem = key_pem or open(os.environ["VOX_APNS_KEY_PATH"], "rb").read()
        self.key = serialization.load_pem_private_key(pem, password=None)
        self.client = httpx.Client(http2=True, timeout=10)
        self._jwt, self._jwt_at = None, 0.0

    def jwt(self) -> str:
        if self._jwt is None or time.time() - self._jwt_at > 40 * 60:  # Apple: refresh every 20-60 min
            b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
            head = b64(json.dumps({"alg": "ES256", "kid": self.key_id}).encode())
            self._jwt_at = time.time()
            claims = b64(json.dumps({"iss": self.team_id, "iat": int(self._jwt_at)}).encode())
            r, s = decode_dss_signature(self.key.sign(f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
            self._jwt = f"{head}.{claims}.{b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
        return self._jwt

    def __call__(self, token, env, payload, headers) -> int:
        resp = self.client.post(f"{self.HOSTS[env]}/3/device/{token}", json=payload,
                                headers=headers | {"authorization": f"bearer {self.jwt()}", "apns-topic": self.topic})
        if resp.status_code != 200:
            log.info("apns_rejected", extra={"status": resp.status_code, "code": resp.json().get("reason") if resp.content else None})
        return resp.status_code


APPROVAL_TTL = {"high": timedelta(minutes=5), "medium": timedelta(minutes=15), "low": timedelta(minutes=15)}

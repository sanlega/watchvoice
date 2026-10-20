"""Shared API plumbing: problem responses, device auth, scopes, signed requests."""
import uuid

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from . import db, devicesig
from .processor import sha256


class Problem(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        self.status, self.code, self.message, self.headers = status, code, message, headers


def problem_response(status, code, message, headers=None):
    return JSONResponse({"code": code, "message": message, "trace_id": str(uuid.uuid4())}, status, headers)


def engine(request: Request):
    return request.app.state.engine


def device(request: Request, authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise Problem(401, "unauthorized", "Missing bearer token", {"WWW-Authenticate": "Bearer"})
    with engine(request).connect() as conn:
        row = conn.execute(select(db.devices).where(
            db.devices.c.credential_hash == sha256(authorization[7:].strip()))).first()
    if row is None or row.revoked_at is not None:
        raise Problem(401, "unauthorized", "Invalid or revoked credential", {"WWW-Authenticate": "Bearer"})
    return row


def require(dev, scope):
    if scope not in (dev.scopes or []):
        raise Problem(403, "forbidden", f"Device lacks scope {scope}")


def require_idempotency_key(key: str | None):
    if not key or not 8 <= len(key) <= 128:
        raise Problem(400, "idempotency_key_required", "Idempotency-Key header (8-128 chars) is required")


def require_signature(dev, message: dict, signature: str | None):
    """ADR-0001 §5: decisions, cancel and pause must be signed by the device's Secure Enclave key."""
    if error := devicesig.verify(dev, message, signature):
        raise Problem(403, error, "Device signature missing, invalid or stale")

"""Device-signed requests (ADR-0001 §5). The watch signs canonical JSON with a
Secure Enclave P-256 key (CryptoKit `signature(for:).derRepresentation`)."""
import base64
import json
from datetime import timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from . import db

MAX_SKEW = timedelta(minutes=2)


def canonical(message: dict) -> bytes:
    return json.dumps(message, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def load_public_key(b64_x963: str) -> ec.EllipticCurvePublicKey:
    """CryptoKit `publicKey.x963Representation`, base64. Raises ValueError if malformed."""
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), base64.b64decode(b64_x963, validate=True))


def verify(device, message: dict, signature_b64: str | None) -> str | None:
    """Returns an error code, or None when the signature is valid and fresh."""
    if not device.public_key:
        return "device_key_missing"
    if not signature_b64:
        return "signature_required"
    try:
        ts = db.parse_ts(message["ts"])
    except (KeyError, ValueError):
        return "timestamp_invalid"
    if abs(db.now() - ts) > MAX_SKEW:
        return "timestamp_skew"
    try:
        load_public_key(device.public_key).verify(base64.b64decode(signature_b64, validate=True),
                                                  canonical(message), ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError):
        return "signature_invalid"
    return None

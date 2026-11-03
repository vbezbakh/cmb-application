"""HMAC signatures for payment webhooks.

Header format: ``Payment-Signature: t=<unix seconds>,v1=<hex sha256 hmac>``
The signed message is ``"<t>." + raw request body``. Including the timestamp in the signed
message lets us reject replays of old deliveries; accepting several ``v1`` values and several
secrets lets the provider and us rotate the secret without downtime.
"""

import hashlib
import hmac
import time
from collections.abc import Sequence

HEADER = "Payment-Signature"


class SignatureError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason  # missing | malformed | stale | mismatch


def compute(secret: str, timestamp: int, body: bytes) -> str:
    message = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def build_header(secret: str, body: bytes, timestamp: int | None = None) -> str:
    timestamp = int(time.time()) if timestamp is None else timestamp
    return f"t={timestamp},v1={compute(secret, timestamp, body)}"


def _parse(header: str) -> tuple[int, list[str]]:
    timestamp: int | None = None
    candidates: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise SignatureError("malformed") from exc
        elif key == "v1" and value:
            candidates.append(value)
    if timestamp is None or not candidates:
        raise SignatureError("malformed")
    return timestamp, candidates


def verify(
    header: str | None,
    body: bytes,
    secrets: Sequence[str],
    tolerance_seconds: int,
    now: float | None = None,
) -> None:
    """Raise SignatureError unless the header carries a valid, fresh signature for body."""
    if not header:
        raise SignatureError("missing")
    timestamp, candidates = _parse(header)
    now = time.time() if now is None else now
    if abs(now - timestamp) > tolerance_seconds:
        raise SignatureError("stale")
    for secret in secrets:
        expected = compute(secret, timestamp, body)
        if any(hmac.compare_digest(expected, candidate) for candidate in candidates):
            return
    raise SignatureError("mismatch")

"""Payment provider webhooks.

Design rules:
- Verify the signature over the raw bytes before parsing anything.
- Acknowledge fast. Providers time out and retry slow endpoints, so the handler only
  records the event; reconciliation happens later (see app.batch).
- Be idempotent. Providers deliver at-least-once, so the event id is the primary key and a
  redelivery is answered 200 "duplicate" without being stored twice.
"""

import json
import logging

from fastapi import APIRouter, HTTPException, Request, status

from app import signatures
from app.metrics import WEBHOOK_EVENTS
from app.routes import DB, AppSettings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/payments")
async def payment_webhook(request: Request, db: DB, settings: AppSettings) -> dict[str, str]:
    declared_length = request.headers.get("content-length", "0")
    if declared_length.isdigit() and int(declared_length) > settings.webhook_max_body_bytes:
        WEBHOOK_EVENTS.labels("rejected_too_large").inc()
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "payload too large")

    body = await request.body()
    if len(body) > settings.webhook_max_body_bytes:
        WEBHOOK_EVENTS.labels("rejected_too_large").inc()
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "payload too large")

    try:
        signatures.verify(
            request.headers.get(signatures.HEADER),
            body,
            settings.webhook_signing_secrets,
            settings.webhook_tolerance_seconds,
        )
    except signatures.SignatureError as exc:
        WEBHOOK_EVENTS.labels(f"rejected_{exc.reason}").inc()
        log.warning("webhook signature rejected", extra={"reason": exc.reason})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid signature") from exc

    try:
        event = json.loads(body)
        event_id, event_type = event["id"], event["type"]
        if not (isinstance(event_id, str) and isinstance(event_type, str)):
            raise TypeError("id and type must be strings")
        if not (0 < len(event_id) <= 255 and 0 < len(event_type) <= 255):
            raise ValueError("id and type must be 1-255 characters")
    except (ValueError, KeyError, TypeError) as exc:
        WEBHOOK_EVENTS.labels("rejected_malformed").inc()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "malformed event") from exc

    async with db.connection() as conn:
        inserted = await conn.fetchval(
            """
            INSERT INTO payment_events (event_id, event_type, payload)
            VALUES ($1, $2, $3::jsonb)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING true
            """,
            event_id,
            event_type,
            body.decode(),
        )

    outcome = "accepted" if inserted else "duplicate"
    WEBHOOK_EVENTS.labels(outcome).inc()
    log.info(
        "webhook stored",
        extra={"event_id": event_id, "event_type": event_type, "outcome": outcome},
    )
    return {"status": outcome}

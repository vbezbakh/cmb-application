import json

from fastapi.testclient import TestClient

from app import signatures
from app.main import create_app
from tests.conftest import WEBHOOK_SECRET, make_settings, requires_db

pytestmark = requires_db


def post_webhook(client: TestClient, event: dict[str, object], secret: str = WEBHOOK_SECRET):
    body = json.dumps(event).encode()
    return client.post(
        "/webhooks/payments",
        content=body,
        headers={
            "content-type": "application/json",
            signatures.HEADER: signatures.build_header(secret, body),
        },
    )


def test_probes(client: TestClient) -> None:
    assert client.get("/livez").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}
    assert client.get("/healthz/dependencies").json() == {"primary": "ok"}


def test_create_then_read_item(client: TestClient) -> None:
    created = client.post("/api/v1/items", json={"name": "coffee"})
    assert created.status_code == 201
    item = created.json()

    assert client.get(f"/api/v1/items/{item['id']}").json() == item
    assert client.get("/api/v1/items").json() == [item]
    assert client.get("/api/v1/items/999999").status_code == 404


def test_request_id_is_echoed_or_generated(client: TestClient) -> None:
    assert client.get("/livez", headers={"x-request-id": "abc"}).headers["x-request-id"] == "abc"
    assert len(client.get("/livez").headers["x-request-id"]) == 32


def test_webhook_is_stored_once_even_when_redelivered(client: TestClient) -> None:
    event = {"id": "evt_123", "type": "payment.succeeded", "data": {"amount": 999}}

    first = post_webhook(client, event)
    second = post_webhook(client, event)

    assert (first.status_code, first.json()) == (200, {"status": "accepted"})
    assert (second.status_code, second.json()) == (200, {"status": "duplicate"})


def test_webhook_with_bad_signature_is_rejected(client: TestClient) -> None:
    response = post_webhook(client, {"id": "evt_1", "type": "x"}, secret="wrong")
    assert response.status_code == 401


def test_webhook_without_id_is_rejected(client: TestClient) -> None:
    assert post_webhook(client, {"type": "payment.succeeded"}).status_code == 400


def test_oversized_webhook_is_rejected(client: TestClient) -> None:
    event = {"id": "evt_big", "type": "x", "data": "a" * 300_000}
    assert post_webhook(client, event).status_code == 413


def test_metrics_expose_request_and_webhook_counters(client: TestClient) -> None:
    client.get("/api/v1/items")
    post_webhook(client, {"id": "evt_m", "type": "payment.succeeded"})

    text = client.get("/metrics").text

    assert 'http_requests_total{method="GET",route="/api/v1/items",status="200"}' in text
    assert 'payment_webhooks_total{outcome="accepted"}' in text
    assert 'db_pool_connections{pool="primary",state="idle"}' in text


def test_database_outage_returns_503_with_retry_after(client: TestClient) -> None:
    db = client.app.state.db
    primary, db.primary = db.primary, None
    try:
        response = client.get("/api/v1/items/1")
    finally:
        db.primary = primary
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"


def test_unreachable_read_replica_does_not_stop_the_api(clean_db: None) -> None:
    settings = make_settings(database_read_url="postgresql://app:app@127.0.0.1:1/app")
    with TestClient(create_app(settings)) as client:
        assert client.get("/readyz").status_code == 200
        created = client.post("/api/v1/items", json={"name": "still works"}).json()
        # The list endpoint normally reads from the replica; it falls back to the primary.
        assert client.get("/api/v1/items").json() == [created]
        assert client.get("/healthz/dependencies").json() == {"primary": "ok"}


def test_components_only_serve_their_own_routes(clean_db: None) -> None:
    with TestClient(create_app(make_settings(component="webhooks"))) as webhooks_only:
        assert webhooks_only.get("/api/v1/items").status_code == 404
        assert post_webhook(webhooks_only, {"id": "evt_w", "type": "x"}).status_code == 200

    with TestClient(create_app(make_settings(component="api"))) as api_only:
        assert api_only.get("/api/v1/items").status_code == 200
        assert api_only.post("/webhooks/payments", content=b"{}").status_code == 404

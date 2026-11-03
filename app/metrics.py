"""Prometheus metrics and the middleware that records them (plus one access log line per request).

The middleware is plain ASGI rather than Starlette's BaseHTTPMiddleware: at a few thousand
requests per second the extra task and memory stream per request add up.
"""

import logging
import time
import uuid
from collections.abc import Iterator

from prometheus_client import REGISTRY, Counter, Gauge, Histogram
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.db import Database

log = logging.getLogger("app.access")

HTTP_REQUESTS = Counter(
    "http_requests_total", "HTTP requests served.", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "Time from request received to response sent.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
HTTP_IN_FLIGHT = Gauge("http_requests_in_flight", "Requests currently being handled.")
WEBHOOK_EVENTS = Counter(
    "payment_webhooks_total",
    "Payment webhook deliveries by outcome (accepted, duplicate, rejected_*).",
    ["outcome"],
)
DB_UNAVAILABLE = Counter(
    "db_unavailable_errors_total",
    "Requests answered with 503 because the database could not be reached.",
)
APP_INFO = Gauge("app_info", "Always 1; labels describe the running build.", ["version", "component"])


class PoolCollector(Collector):
    """Reports pool usage at scrape time. Pool exhaustion shows up here before it shows up as latency."""

    def __init__(self) -> None:
        self.db: Database | None = None

    def collect(self) -> Iterator[Metric]:
        if self.db is None:
            return
        connections = GaugeMetricFamily(
            "db_pool_connections", "Pooled connections by state.", labels=["pool", "state"]
        )
        limit = GaugeMetricFamily(
            "db_pool_max_connections", "Configured pool size.", labels=["pool"]
        )
        for name, pool in self.db.pools():
            idle = pool.get_idle_size()
            connections.add_metric([name, "in_use"], pool.get_size() - idle)
            connections.add_metric([name, "idle"], idle)
            limit.add_metric([name], pool.get_max_size())
        yield connections
        yield limit


POOL_COLLECTOR = PoolCollector()
REGISTRY.register(POOL_COLLECTOR)

# Probes and scrapes would drown out real traffic in the access log.
_QUIET_PATHS = frozenset({"/livez", "/readyz", "/metrics"})


class ObservabilityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        request_id = _header(scope, b"x-request-id") or uuid.uuid4().hex
        status = 500  # what the client sees if the app raises before responding

        async def send_with_request_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = [*message.get("headers", []), (b"x-request-id", request_id.encode())]
                message = {**message, "headers": headers}
            await send(message)

        HTTP_IN_FLIGHT.inc()
        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            HTTP_IN_FLIGHT.dec()
            elapsed = time.perf_counter() - start
            # Label by route template (/api/v1/items/{item_id}), never the raw path,
            # so metric cardinality stays bounded.
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            method = scope["method"]
            HTTP_REQUESTS.labels(method, route, str(status)).inc()
            HTTP_LATENCY.labels(method, route).observe(elapsed)
            if scope["path"] not in _QUIET_PATHS:
                log.info(
                    "request",
                    extra={
                        "request_id": request_id,
                        "method": method,
                        "route": route,
                        "path": scope["path"],
                        "status": status,
                        "duration_ms": round(elapsed * 1000, 2),
                    },
                )


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")[:128]
    return None

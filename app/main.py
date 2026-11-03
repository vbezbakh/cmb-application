"""ASGI application factory.

One image runs several workloads. COMPONENT decides which routes a pod serves:
  api      - mobile-facing API (/api/v1/...)
  webhooks - payment provider webhooks (/webhooks/...)
  all      - everything; for local development and tests
Splitting them means a spike in mobile traffic cannot starve payment webhooks of workers,
and each can be scaled and rolled out independently.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import ConfigError, Settings
from app.db import TRANSIENT_DB_ERRORS, Database
from app.metrics import APP_INFO, DB_UNAVAILABLE, POOL_COLLECTOR, ObservabilityMiddleware
from app.routes import health, items, webhooks

log = logging.getLogger(__name__)

ROUTERS = {
    "api": [items.router],
    "webhooks": [webhooks.router],
    "all": [items.router, webhooks.router],
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    db = Database(settings)
    await db.connect()
    app.state.db = db
    POOL_COLLECTOR.db = db
    APP_INFO.labels(settings.app_version, settings.component).set(1)
    log.info("started", extra={"version": settings.app_version})
    try:
        yield
    finally:
        # Uvicorn only gets here after in-flight requests have finished (or the graceful
        # shutdown timeout has passed), so closing the pools cannot cut off a live query.
        log.info("shutting down")
        POOL_COLLECTOR.db = None
        await db.close()


async def database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    DB_UNAVAILABLE.inc()
    log.warning("database unavailable", extra={"error": repr(exc), "path": request.url.path})
    return JSONResponse(
        status_code=503,
        content={"detail": "database temporarily unavailable, retry shortly"},
        headers={"Retry-After": "1"},
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if settings.component not in ROUTERS:
        raise ConfigError(f"COMPONENT={settings.component!r} does not serve HTTP")
    if settings.component in ("webhooks", "all") and not settings.webhook_signing_secrets:
        raise ConfigError("WEBHOOK_SIGNING_SECRET is required to serve webhooks")

    app = FastAPI(title="cmb-application", version=settings.app_version, lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(ObservabilityMiddleware)
    for exc_type in TRANSIENT_DB_ERRORS:
        app.add_exception_handler(exc_type, database_unavailable)

    app.include_router(health.router)
    for router in ROUTERS[settings.component]:
        app.include_router(router)
    return app

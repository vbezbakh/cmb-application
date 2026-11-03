"""Probe, version and metrics endpoints.

/livez   - the process is alive and its event loop is responsive. No dependencies, ever:
           a liveness probe that checks the database restarts every pod during a DB blip.
/readyz  - this pod has finished starting up (pools open) and can take traffic.
           It deliberately does NOT ping the database on every probe: if the database
           blips, every pod would go unready at once, the ingress would have no backends,
           and recovery would wait for the next probe round. Pods stay in rotation and
           answer 503 + Retry-After until the database is back.
/healthz/dependencies - an explicit database check for dashboards and humans, not for kubelet.
"""

import asyncio

from fastapi import APIRouter, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.db import TRANSIENT_DB_ERRORS
from app.routes import DB, AppSettings

router = APIRouter(tags=["health"])


@router.get("/livez", include_in_schema=False)
async def livez() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
async def readyz(db: DB, response: Response) -> dict[str, str]:
    if db.primary is None:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "starting"}
    return {"status": "ready"}


@router.get("/healthz/dependencies")
async def dependencies(db: DB, response: Response) -> dict[str, str]:
    results: dict[str, str] = {}
    for name, pool in db.pools():
        try:
            async with asyncio.timeout(1):
                await pool.fetchval("SELECT 1")
            results[name] = "ok"
        except TRANSIENT_DB_ERRORS as exc:
            results[name] = f"unavailable: {type(exc).__name__}"
    if not results or any(v != "ok" for v in results.values()):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return results


@router.get("/version")
async def version(settings: AppSettings) -> dict[str, str]:
    return {"version": settings.app_version, "component": settings.component}


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

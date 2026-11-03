# cmb-application

[![CI](https://github.com/vbezbakh/cmb-application/actions/workflows/ci.yml/badge.svg)](https://github.com/vbezbakh/cmb-application/actions/workflows/ci.yml)

A FastAPI service built to run well on Kubernetes: an HTTP API for the mobile app, signed payment webhooks, a daily batch job and schema migrations, all from one image.

> [!NOTE]
> This is a compact stand-in for the real service, written to exercise the platform around it: probes, graceful shutdown, connection pooling, idempotency and observability. **The deployment, its decisions and a one-command local environment are in [cmb-deployment](https://github.com/vbezbakh/cmb-deployment).**

## One image, four jobs

| Command | `COMPONENT` | Runs as | Serves |
|---|---|---|---|
| `python -m app` | `api` | Deployment | `/api/v1/items` |
| `python -m app` | `webhooks` | Deployment | `/webhooks/payments` |
| `python -m app.batch [--date YYYY-MM-DD]` | `batch` | CronJob | Daily summary of payment events |
| `python -m app.migrate` | `migrate` | Pre-deploy hook Job | Pending SQL in `app/migrations/` |

Every pod also serves `/livez`, `/readyz`, `/healthz/dependencies`, `/version` and `/metrics`.

## Built for Kubernetes

| Behaviour | Why it matters |
|---|---|
| **Liveness and readiness never touch the database** | A DB blip must not restart pods or pull all of them out of the load balancer at once. DB errors become **503 + `Retry-After`**, never 500. |
| **Graceful shutdown** | On SIGTERM uvicorn stops accepting, drains in-flight requests (`GRACEFUL_SHUTDOWN_SECONDS`), then closes the pools. |
| **Keep-alive 75s** | Longer than the ingress's 60s upstream keep-alive, which avoids sporadic 502s from reused closed connections. |
| **One process per container** | Kubernetes scales pods; CPU requests, probes and metrics all map to exactly one process. |
| **Startup retries** | Pools retry with backoff for up to `DB_STARTUP_TIMEOUT_SECONDS`, so a pod starting during a DB failover connects instead of crash-looping. |
| **PgBouncer-safe** | Named prepared statements off (`DB_STATEMENT_CACHE_SIZE=0`), transaction-scoped advisory locks only. |
| **Bounded waits** | Pool acquire timeout (2s) and query timeout (5s): overload fails fast instead of piling up. |
| **Read/write split** | Writes and read-your-own-write go to the primary; the list endpoint uses `DATABASE_READ_URL` when set. |
| **Signed, idempotent webhooks** | HMAC-SHA256 over `timestamp.body`, 5-minute replay window, constant-time compare, multiple secrets for rotation; the provider's event id is the primary key, so redeliveries are stored once. |
| **Safe batch and migrations** | Idempotent upserts; advisory locks so overlapping runs can't interleave; migrations applied in one transaction. |
| **Observability** | RED metrics labelled by route template (bounded cardinality), webhook outcomes, DB pool usage, `app_info{version}`; JSON logs with a request id echoed in `X-Request-ID`. |
| **Hardened image** | Multi-stage build from `uv.lock`, non-root uid 10001, bytecode precompiled for a read-only root filesystem, ~176 MB. |

## Configuration

All settings are environment variables ([`app/config.py`](app/config.py)); the Helm chart sets them.

| Variable | Default | |
|---|---|---|
| `DATABASE_URL` | required | Primary (through PgBouncer) |
| `DATABASE_READ_URL` | unset | Read replicas; falls back to the primary |
| `WEBHOOK_SIGNING_SECRET` | required for webhooks | Comma-separated to rotate |
| `COMPONENT` | `all` | `api`, `webhooks`, `batch`, `migrate`, or `all` for local development |
| `DB_POOL_MIN_SIZE` / `DB_POOL_MAX_SIZE` | 1 / 10 | Per pod; multiply by replicas when sizing PgBouncer |
| `DB_ACQUIRE_TIMEOUT_SECONDS` / `DB_COMMAND_TIMEOUT_SECONDS` | 2 / 5 | |
| `KEEP_ALIVE_SECONDS` / `GRACEFUL_SHUTDOWN_SECONDS` | 75 / 25 | |
| `LOG_LEVEL` | `INFO` | |

## Pipeline

```mermaid
flowchart LR
  pr[Pull request] --> test[ruff + pytest<br/>against Postgres 18]
  main[Merge to main] --> test
  test --> build[Build image]
  build -->|main only| push[(ghcr.io/vbezbakh/cmb-application<br/>tag = commit SHA)]
  push --> bump[Commit tag + digest to<br/>cmb-deployment staging]
```

Actions are pinned by commit SHA, and Dependabot keeps Python dependencies, the base image and actions up to date.

## Develop

```bash
uv sync
docker run -d --name pg -p 5432:5432 -e POSTGRES_USER=app -e POSTGRES_PASSWORD=app -e POSTGRES_DB=app postgres:18
TEST_DATABASE_URL=postgresql://app:app@localhost:5432/app uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Run it locally:

```bash
export DATABASE_URL=postgresql://app:app@localhost:5432/app WEBHOOK_SIGNING_SECRET=dev-secret
uv run python -m app.migrate
uv run python -m app          # http://localhost:8000/docs
```

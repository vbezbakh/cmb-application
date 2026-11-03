# syntax=docker/dockerfile:1

# ---- build: resolve dependencies from the lockfile into a standalone virtualenv ----
FROM python:3.14-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /src
# Copy only the dependency manifests first: this layer is reused until uv.lock changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- runtime: no build tools, no package manager cache, non-root ----
FROM python:3.14-slim

ARG APP_VERSION=dev
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=$APP_VERSION

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app

COPY --from=build /opt/venv /opt/venv
WORKDIR /srv
COPY app ./app
# Compile at build time so the container works with a read-only root filesystem.
RUN python -m compileall -q app

USER 10001
EXPOSE 8000

# Python is PID 1 and handles SIGTERM itself (uvicorn drains in-flight requests),
# so no init wrapper is needed. Batch and migrations override the command.
CMD ["python", "-m", "app"]

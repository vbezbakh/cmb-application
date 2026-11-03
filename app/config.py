"""Runtime configuration, read once from environment variables.

Everything the platform needs to tune (pool sizes, timeouts, which routes a pod serves)
is an env var, so the Helm chart can change behaviour without a new image.
"""

import os
from dataclasses import dataclass

COMPONENTS = ("all", "api", "webhooks", "batch", "migrate")


class ConfigError(RuntimeError):
    """A required setting is missing or invalid."""


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = _str(name)
    try:
        return int(raw) if raw else default
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _float(name: str, default: float) -> float:
    raw = _str(name)
    try:
        return float(raw) if raw else default
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    database_read_url: str | None = None
    # Comma-separated in the env var so a new secret can be added before the old one is removed.
    webhook_signing_secrets: tuple[str, ...] = ()

    component: str = "all"
    app_version: str = "dev"
    log_level: str = "INFO"

    host: str = "0.0.0.0"  # noqa: S104 - must listen on the pod IP
    port: int = 8000
    # Must exceed the ingress controller's upstream keep-alive timeout (60s for ingress-nginx),
    # otherwise the proxy can reuse a connection we have just closed and return a 502.
    keep_alive_seconds: int = 75
    graceful_shutdown_seconds: int = 25

    db_pool_min_size: int = 1
    db_pool_max_size: int = 10
    db_acquire_timeout_seconds: float = 2.0
    db_command_timeout_seconds: float = 5.0
    db_startup_timeout_seconds: float = 60.0
    # 0 disables named prepared statements, which PgBouncer in transaction mode cannot route.
    db_statement_cache_size: int = 0

    webhook_tolerance_seconds: int = 300
    webhook_max_body_bytes: int = 256 * 1024

    batch_statement_timeout_seconds: float = 1800.0

    @classmethod
    def from_env(cls, component: str | None = None) -> Settings:
        database_url = _str("DATABASE_URL")
        if not database_url:
            raise ConfigError("missing required environment variable DATABASE_URL")

        component = component or _str("COMPONENT", "all")
        if component not in COMPONENTS:
            raise ConfigError(f"COMPONENT must be one of {COMPONENTS}, got {component!r}")

        secrets = tuple(s.strip() for s in _str("WEBHOOK_SIGNING_SECRET").split(",") if s.strip())

        return cls(
            database_url=database_url,
            database_read_url=_str("DATABASE_READ_URL") or None,
            webhook_signing_secrets=secrets,
            component=component,
            app_version=_str("APP_VERSION", "dev"),
            log_level=_str("LOG_LEVEL", "INFO").upper(),
            port=_int("PORT", 8000),
            keep_alive_seconds=_int("KEEP_ALIVE_SECONDS", 75),
            graceful_shutdown_seconds=_int("GRACEFUL_SHUTDOWN_SECONDS", 25),
            db_pool_min_size=_int("DB_POOL_MIN_SIZE", 1),
            db_pool_max_size=_int("DB_POOL_MAX_SIZE", 10),
            db_acquire_timeout_seconds=_float("DB_ACQUIRE_TIMEOUT_SECONDS", 2.0),
            db_command_timeout_seconds=_float("DB_COMMAND_TIMEOUT_SECONDS", 5.0),
            db_startup_timeout_seconds=_float("DB_STARTUP_TIMEOUT_SECONDS", 60.0),
            db_statement_cache_size=_int("DB_STATEMENT_CACHE_SIZE", 0),
            webhook_tolerance_seconds=_int("WEBHOOK_TOLERANCE_SECONDS", 300),
            webhook_max_body_bytes=_int("WEBHOOK_MAX_BODY_BYTES", 256 * 1024),
            batch_statement_timeout_seconds=_float("BATCH_STATEMENT_TIMEOUT_SECONDS", 1800.0),
        )

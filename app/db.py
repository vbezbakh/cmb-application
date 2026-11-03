"""Connection pools for the primary (writes) and, optionally, a read replica endpoint."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import asyncpg

from app.config import Settings

log = logging.getLogger(__name__)


class DatabaseUnavailable(Exception):
    """The pool is not initialised; treated like any other transient database failure."""


# Failures that mean "the database can't be reached right now" (failover, restart, overload),
# as opposed to a bug in a query. These become a 503 with Retry-After instead of a 500.
TRANSIENT_DB_ERRORS: tuple[type[BaseException], ...] = (
    DatabaseUnavailable,
    OSError,
    TimeoutError,
    asyncpg.CannotConnectNowError,
    asyncpg.ConnectionDoesNotExistError,
    asyncpg.ConnectionFailureError,
    asyncpg.AdminShutdownError,
    asyncpg.TooManyConnectionsError,
    asyncpg.ReadOnlySQLTransactionError,
)


class Database:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.primary: asyncpg.Pool | None = None
        self.replica: asyncpg.Pool | None = None

    async def connect(self) -> None:
        """Open the pools, retrying while the database comes up (e.g. right after a failover)."""
        deadline = time.monotonic() + self._settings.db_startup_timeout_seconds
        delay = 0.5
        while True:
            try:
                self.primary = await self._create_pool(self._settings.database_url)
                if self._settings.database_read_url:
                    self.replica = await self._create_pool(self._settings.database_read_url)
                log.info("database pools ready", extra={"read_replica": self.replica is not None})
                return
            except TRANSIENT_DB_ERRORS as exc:
                await self.close()
                if time.monotonic() + delay > deadline:
                    raise
                log.warning(
                    "database not reachable yet, retrying",
                    extra={"error": repr(exc), "retry_in_seconds": delay},
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 5.0)

    async def _create_pool(self, dsn: str) -> asyncpg.Pool:
        s = self._settings
        return await asyncpg.create_pool(
            dsn,
            min_size=s.db_pool_min_size,
            max_size=s.db_pool_max_size,
            timeout=10,
            command_timeout=s.db_command_timeout_seconds,
            statement_cache_size=s.db_statement_cache_size,
            max_inactive_connection_lifetime=300,
            server_settings={"application_name": f"cmb-{s.component}"},
        )

    async def close(self) -> None:
        for pool in (self.replica, self.primary):
            if pool is not None:
                try:
                    await asyncio.wait_for(pool.close(), timeout=10)
                except TimeoutError:
                    pool.terminate()
        self.primary = self.replica = None

    @asynccontextmanager
    async def connection(self, *, readonly: bool = False) -> AsyncIterator[asyncpg.Connection]:
        """Borrow a connection. Read-only callers go to the replica pool when one is configured.

        The acquire timeout matters under load: without it a request waits forever for a free
        connection, and a slow database turns into a pile-up of hung requests.
        """
        pool = (self.replica or self.primary) if readonly else self.primary
        if pool is None:
            raise DatabaseUnavailable("database pool is not initialised")
        async with pool.acquire(timeout=self._settings.db_acquire_timeout_seconds) as conn:
            yield conn

    def pools(self) -> Iterator[tuple[str, asyncpg.Pool]]:
        if self.primary is not None:
            yield "primary", self.primary
        if self.replica is not None:
            yield "replica", self.replica

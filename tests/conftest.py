"""Shared fixtures.

Database tests need a real Postgres: set TEST_DATABASE_URL (CI starts one as a service
container). Without it they are skipped, and the pure unit tests still run.
"""

import asyncio
import os
from collections.abc import Iterator

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.migrate import migrate

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
WEBHOOK_SECRET = "test-signing-secret"

requires_db = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")


def make_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "database_url": TEST_DATABASE_URL,
        "webhook_signing_secrets": (WEBHOOK_SECRET,),
        "component": "all",
        "db_pool_min_size": 1,
        "db_pool_max_size": 4,
        "db_startup_timeout_seconds": 5,
    }
    return Settings(**(defaults | overrides))  # type: ignore[arg-type]


async def _execute(sql: str) -> None:
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def migrated() -> None:
    asyncio.run(migrate(TEST_DATABASE_URL))


@pytest.fixture
def clean_db(migrated: None) -> None:
    asyncio.run(
        _execute(
            "TRUNCATE items, payment_events, daily_payment_summary, batch_runs RESTART IDENTITY"
        )
    )


@pytest.fixture
def client(clean_db: None) -> Iterator[TestClient]:
    with TestClient(create_app(make_settings())) as test_client:
        yield test_client

import asyncio
from datetime import date

import asyncpg

from app import batch
from app.migrate import migrate
from tests.conftest import TEST_DATABASE_URL, make_settings, requires_db

pytestmark = requires_db

DAY = date(2026, 11, 1)


async def _seed_events() -> None:
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.executemany(
            "INSERT INTO payment_events (event_id, event_type, payload, received_at) "
            "VALUES ($1, $2, '{}'::jsonb, $3::timestamptz)",
            [
                ("evt_a", "payment.succeeded", "2026-11-01T00:00:00Z"),
                ("evt_b", "payment.succeeded", "2026-11-01T23:59:59Z"),
                ("evt_c", "payment.refunded", "2026-11-01T12:00:00Z"),
                ("evt_d", "payment.succeeded", "2026-11-02T00:00:00Z"),  # next day
            ],
        )
    finally:
        await conn.close()


async def _summary() -> dict[str, int]:
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        rows = await conn.fetch(
            "SELECT event_type, event_count FROM daily_payment_summary WHERE day = $1", DAY
        )
        return {r["event_type"]: r["event_count"] for r in rows}
    finally:
        await conn.close()


def test_batch_is_idempotent(clean_db: None) -> None:
    asyncio.run(_seed_events())
    settings = make_settings(component="batch")

    first = asyncio.run(batch.run(settings, DAY))
    second = asyncio.run(batch.run(settings, DAY))

    assert first == second == 2
    assert asyncio.run(_summary()) == {"payment.succeeded": 2, "payment.refunded": 1}


def test_overlapping_batch_run_exits_without_doing_work(clean_db: None) -> None:
    async def scenario() -> int | None:
        holder = await asyncpg.connect(TEST_DATABASE_URL)
        try:
            async with holder.transaction():
                await holder.execute("SELECT pg_advisory_xact_lock($1)", batch.LOCK_KEY)
                return await batch.run(make_settings(component="batch"), DAY)
        finally:
            await holder.close()

    assert asyncio.run(scenario()) is None


def test_migrations_are_applied_once(migrated: None) -> None:
    assert asyncio.run(migrate(TEST_DATABASE_URL)) == []

import asyncio
from datetime import UTC, date, datetime

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
            "VALUES ($1, $2, '{}'::jsonb, $3)",
            [
                ("evt_a", "payment.succeeded", datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC)),
                ("evt_b", "payment.succeeded", datetime(2026, 11, 1, 23, 59, 59, tzinfo=UTC)),
                ("evt_c", "payment.refunded", datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)),
                ("evt_d", "payment.succeeded", datetime(2026, 11, 2, 0, 0, 0, tzinfo=UTC)),
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

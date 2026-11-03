"""Daily batch job: summarise one UTC day of payment events.

    python -m app.batch                    # yesterday (UTC)
    python -m app.batch --date 2026-11-01  # backfill or re-run a specific day

Safe to run more than once:
- Idempotent. The summary is an upsert keyed by (day, event_type), so a retry or a manual
  re-run recomputes the same rows instead of double counting.
- No overlap. Kubernetes CronJobs can, rarely, start the same run twice even with
  concurrencyPolicy: Forbid. A transaction-scoped advisory lock makes the second run exit
  cleanly. It is transaction-scoped (not session-scoped) on purpose: PgBouncer in transaction
  mode can hand the session to someone else between statements.
"""

import argparse
import asyncio
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime

import asyncpg

from app.config import Settings
from app.logs import configure_logging

log = logging.getLogger("app.batch")

LOCK_KEY = 7_291_004_101  # any constant shared by every copy of this job

SUMMARY_SQL = """
INSERT INTO daily_payment_summary (day, event_type, event_count, computed_at)
SELECT $3::date, event_type, count(*), now()
FROM payment_events
WHERE received_at >= $1 AND received_at < $2
GROUP BY event_type
ON CONFLICT (day, event_type) DO UPDATE
SET event_count = EXCLUDED.event_count, computed_at = EXCLUDED.computed_at
"""

RECORD_RUN_SQL = """
INSERT INTO batch_runs (run_date, rows_written, finished_at)
VALUES ($1, $2, now())
ON CONFLICT (run_date) DO UPDATE
SET rows_written = EXCLUDED.rows_written, finished_at = EXCLUDED.finished_at
"""


async def run(settings: Settings, day: date) -> int | None:
    """Summarise `day`. Returns rows written, or None if another run holds the lock."""
    conn = await asyncpg.connect(
        settings.database_url,
        timeout=30,
        command_timeout=settings.batch_statement_timeout_seconds,
        statement_cache_size=settings.db_statement_cache_size,
        server_settings={"application_name": "cmb-batch"},
    )
    try:
        async with conn.transaction():
            if not await conn.fetchval("SELECT pg_try_advisory_xact_lock($1)", LOCK_KEY):
                log.warning("another run holds the lock, exiting", extra={"day": day})
                return None
            start = datetime.combine(day, dtime.min, tzinfo=UTC)
            result = await conn.execute(SUMMARY_SQL, start, start + timedelta(days=1), day)
            rows = int(result.split()[-1])  # "INSERT 0 <n>"
            await conn.execute(RECORD_RUN_SQL, day, rows)
        return rows
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.batch", description="Summarise one UTC day of payment events."
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="UTC day to summarise, YYYY-MM-DD (default: yesterday)",
    )
    args = parser.parse_args(argv)

    settings = Settings.from_env(component="batch")
    configure_logging(settings.log_level, settings.component)
    day = args.date or datetime.now(UTC).date() - timedelta(days=1)

    log.info("batch started", extra={"day": day, "version": settings.app_version})
    try:
        rows = asyncio.run(run(settings, day))
    except Exception:
        # Non-zero exit marks the Job pod failed, so the Job retries (backoffLimit)
        # and the failure is visible to kube-state-metrics alerts.
        log.exception("batch failed", extra={"day": day})
        return 1
    log.info("batch finished", extra={"day": day, "rows_written": rows})
    return 0


if __name__ == "__main__":
    sys.exit(main())

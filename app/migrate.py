"""Apply pending SQL migrations: ``python -m app.migrate``.

Runs as a pre-deploy hook Job, before new pods start. All pending files are applied in one
transaction under an advisory lock, so two concurrent runs cannot interleave and a failed
migration leaves the schema untouched.

Migrations must be backwards compatible with the version still serving traffic during a
rolling update (expand first, contract in a later release).
"""

import asyncio
import logging
import sys
from pathlib import Path

import asyncpg

from app.config import Settings
from app.logs import configure_logging

log = logging.getLogger("app.migrate")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
LOCK_KEY = 7_291_004_102


def load_migrations(migrations_dir: Path) -> list[tuple[str, str]]:
    return [(path.stem, path.read_text()) for path in sorted(migrations_dir.glob("*.sql"))]


async def migrate(database_url: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations and return the versions applied by this call."""
    migrations = await asyncio.to_thread(load_migrations, migrations_dir)
    conn = await asyncpg.connect(
        database_url,
        timeout=30,
        statement_cache_size=0,
        server_settings={"application_name": "cmb-migrate"},
    )
    applied: list[str] = []
    try:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", LOCK_KEY)
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version    text        PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            done = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
            for version, sql in migrations:
                if version in done:
                    continue
                log.info("applying migration", extra={"version": version})
                await conn.execute(sql)
                await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", version)
                applied.append(version)
    finally:
        await conn.close()
    return applied


def main() -> int:
    settings = Settings.from_env(component="migrate")
    configure_logging(settings.log_level, settings.component)
    try:
        applied = asyncio.run(migrate(settings.database_url))
    except Exception:
        log.exception("migration failed")
        return 1
    log.info("migrations complete", extra={"applied": applied})
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Copy a SQLite snapshot into an EMPTY PostgreSQL database, without altering SQLite.

Run `uv run python scripts/migrate_sqlite_to_postgres.py` to inspect counts first.
Set DATABASE_URL to the PostgreSQL destination; --apply performs the copy.
Stop the backend while migrating so the snapshot remains the cutover point.
"""

import argparse
import asyncio
import sqlite3
import tempfile
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import create_async_engine

from medical_agent.core.config import get_settings
from medical_agent.core.database import Base


async def migrate(source: Path, *, apply: bool) -> None:
    if not source.is_file():
        raise ValueError("SQLite source does not exist")
    for module in ("conversation", "knowledge", "media", "system"):
        import_module(f"medical_agent.modules.{module}.models")
    target_url = get_settings().database_url
    if not target_url.startswith("postgresql+asyncpg://"):
        raise ValueError("Set DATABASE_URL to a postgresql+asyncpg destination first")
    # SQLite's backup API yields a consistent snapshot including WAL data.
    with tempfile.TemporaryDirectory(prefix="medical-db-copy-") as directory:
        snapshot = Path(directory) / "snapshot.db"
        source_db = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
        snapshot_db = sqlite3.connect(snapshot)
        try:
            source_db.backup(snapshot_db)
        finally:
            snapshot_db.close()
            source_db.close()
        origin = create_async_engine(f"sqlite+aiosqlite:///{snapshot.as_posix()}")
        destination = create_async_engine(target_url)
        try:
            async with origin.connect() as src, destination.begin() as dst:
                names = await src.run_sync(lambda connection: inspect(connection).get_table_names())
                target_names = await dst.run_sync(
                    lambda connection: inspect(connection).get_table_names()
                )
                tables = [table for table in Base.metadata.sorted_tables if table.name in names]
                for table in Base.metadata.sorted_tables:
                    if table.name not in target_names:
                        raise ValueError("Run `uv run alembic upgrade head` on the target first")
                    if await dst.scalar(select(func.count()).select_from(table)):
                        raise ValueError(
                            f"Destination is not empty: {table.name}; nothing was copied"
                        )
                for table in tables:
                    count = await src.scalar(select(func.count()).select_from(table))
                    print(f"{table.name}: {count} rows")
                    if not apply:
                        continue
                    result = await src.stream(select(table))
                    async for batch in result.mappings().partitions(500):
                        await dst.execute(
                            table.insert(),
                            [
                                {
                                    key: value.replace(tzinfo=UTC)
                                    if isinstance(value, datetime) and value.tzinfo is None
                                    else value
                                    for key, value in row.items()
                                }
                                for row in batch
                            ],
                        )
                    copied = await dst.scalar(select(func.count()).select_from(table))
                    if copied != count:
                        raise ValueError(f"Count mismatch: {table.name}")
                print(
                    "Copy committed; row counts verified."
                    if apply
                    else "Preview only; use --apply to copy."
                )
        finally:
            await origin.dispose()
            await destination.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("medical_agent.db"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(migrate(args.source, apply=args.apply))

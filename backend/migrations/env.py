"""Async Alembic environment; uses DATABASE_URL without printing credentials."""

import asyncio

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from medical_agent.core.config import get_settings
from medical_agent.core.database import Base, load_model_metadata

load_model_metadata()


def migrate(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run() -> None:
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as connection:
        await connection.run_sync(migrate)
    await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Use online migrations so existing SQLite tables can be detected safely.")
asyncio.run(run())

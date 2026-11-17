"""Async database engine, sessions, and transaction helpers."""

from collections.abc import AsyncIterator
from importlib import import_module
from typing import Any

from sqlalchemy import Connection, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from medical_agent.core.config import Settings, get_settings


class Base(DeclarativeBase):
    pass


MODEL_MODULES = (
    "medical_agent.modules.announcements.models",
    "medical_agent.modules.conversation.models",
    "medical_agent.modules.channels.common.models",
    "medical_agent.modules.knowledge.models",
    "medical_agent.modules.media.models",
    "medical_agent.modules.system.models",
)


def load_model_metadata() -> None:
    """Import every ORM module before schema inspection or data migration."""
    for module in MODEL_MODULES:
        import_module(module)


def database_engine_options(settings: Settings) -> dict[str, Any]:
    """Return runtime engine options supported by the configured database."""
    options: dict[str, Any] = {"echo": False}
    if make_url(settings.database_url).get_backend_name() != "postgresql":
        return options

    # QueuePool limits are intentionally PostgreSQL-only. Increasing an SQLite
    # pool adds writers but cannot remove SQLite's database-wide write lock.
    options.update(
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_recycle=settings.database_pool_recycle_seconds,
        pool_pre_ping=settings.database_pool_pre_ping,
    )
    return options


settings = get_settings()
engine = create_async_engine(settings.database_url, **database_engine_options(settings))
session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


async def init_db() -> None:
    load_model_metadata()

    def verify_existing_schema(connection: Connection) -> None:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        missing: list[str] = []
        for table in Base.metadata.sorted_tables:
            if table.name not in tables:
                continue
            columns = {item["name"] for item in inspector.get_columns(table.name)}
            missing.extend(
                f"{table.name}.{column.name}"
                for column in table.columns
                if column.name not in columns
            )
        if missing:
            raise RuntimeError(
                "Database migration required (" + ", ".join(missing) + "). "
                "Run in backend: uv run python scripts/upgrade_database.py; then restart the API."
            )

    async with engine.connect() as connection:
        await connection.run_sync(verify_existing_schema)

    if settings.app_env == "production":
        # Production schemas are changed explicitly with `alembic upgrade head`.
        return

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    await engine.dispose()
